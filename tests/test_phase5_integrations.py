from __future__ import annotations

import hashlib
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from soleresearch.errors import ProjectError, SchemaError
from soleresearch.cli import _parser
from soleresearch.controller import capability_paths
from soleresearch.integrations import (
    approve_adapter,
    generate_adapter,
    prepare_zotero_bundle,
    test_adapter as verify_adapter,
)
from soleresearch.project import initialize_project
from soleresearch.orchestration import RunRepository
from soleresearch.schemas import capability_spec, schema_spec, tool_catalog, validate_document
from soleresearch.sources import SourceRepository, import_local_document

PLUGIN = Path(__file__).parents[1] / "integrations/codex/soleresearch"
ORCHESTRATE = PLUGIN / "skills/orchestrate-research"
ZOTERO = PLUGIN / "skills/prepare-zotero-bundle"


def _cli(env: dict[str, str], *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "soleresearch", *arguments],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
        env=env,
    )


def _console_script() -> Path:
    executable = Path(sys.executable).with_name("sole-research")
    assert executable.is_file()
    return executable


def test_tool_catalog_is_strict_sorted_and_matches_capability_commands() -> None:
    catalog = tool_catalog()
    identities = [item["id"] for item in catalog["tools"]]
    assert identities == sorted(identities)
    assert len(identities) == len(set(identities)) == 23
    assert schema_spec("tool_catalog")["properties"]["catalog_version"] == {"type": "integer", "const": 1}
    commands = capability_spec()["commands"]
    represented = {item["command"].split()[1] for item in catalog["tools"]}
    assert represented == set(commands)
    assert {"tools", "adapter", "zotero-bundle"} <= set(commands)
    with pytest.raises(SchemaError, match="unsupported tool_catalog schema_version"):
        validate_document("tool_catalog", {**catalog, "schema_version": 2})


def test_tool_catalog_cli_lists_and_fails_closed_on_unknown_id(tmp_path: Path) -> None:
    env = {**os.environ, "SOLERESEARCH_CONFIG_HOME": str(tmp_path / "config")}
    listed = _cli(env, "tools", "list")
    assert listed.returncode == 0 and json.loads(listed.stdout) == tool_catalog()
    shown = _cli(env, "tools", "show", "--tool-id", "run.operate")
    assert shown.returncode == 0 and json.loads(shown.stdout)["tool"]["authority"] == "action_dependent"
    unknown = _cli(env, "tools", "show", "--tool-id", "missing.tool")
    error = json.loads(unknown.stderr)
    assert unknown.returncode == 2 and error["command"] == "tools"
    assert "unknown tool_id" in error["error"]


def test_canonical_result_preflight_accepts_valid_and_rejects_harness_malformed(tmp_path: Path) -> None:
    env = {**os.environ, "SOLERESEARCH_CONFIG_HOME": str(tmp_path / "config")}
    result = {
        "schema_version": 1,
        "run_id": "run_" + "1" * 32,
        "task_id": "tsk_" + "1" * 32,
        "worker_id": "wrk_" + "1" * 32,
        "base_revision": 0,
        "used_capabilities": [],
        "used_artifact_references": [],
        "accessed_sources": [],
        "evidence": [],
        "proposed_graph_operations": [],
        "outline_suggestions": [],
        "disagreements": [],
        "gaps": [],
        "rationale": "No standalone outputs.",
        "usage": {"unit": "tokens", "amount": 0},
        "completed_operations": [],
        "errors": [],
        "completed_at": "2026-07-10T12:01:00Z",
    }
    packet = tmp_path / "result.json"
    packet.write_text(json.dumps(result), encoding="utf-8")
    accepted = _cli(env, "tools", "validate-result", "--file", str(packet))
    assert accepted.returncode == 0
    assert json.loads(accepted.stdout) == {
        "schema_version": 1, "kind": "result", "valid": True, "file": str(packet),
    }

    result["proposed_graph_operations"] = [
        {"op": "add", "entity": "node", "node": {"node_type": "question", "title": "Malformed"}}
    ]
    result["completed_operations"] = ["propose_graph_diff"]
    packet.write_text(json.dumps(result), encoding="utf-8")
    rejected = _cli(env, "tools", "validate-result", "--file", str(packet))
    assert rejected.returncode == 2
    error = json.loads(rejected.stderr)
    assert error["command"] == "tools" and "graph operation" in error["error"]


def test_authorized_bundle_command_and_bundle_aware_result_preflight(tmp_path: Path) -> None:
    env = {**os.environ, "SOLERESEARCH_CONFIG_HOME": str(tmp_path / "config")}
    os.environ["SOLERESEARCH_CONFIG_HOME"] = env["SOLERESEARCH_CONFIG_HOME"]
    project = tmp_path / "project"
    document = initialize_project(project)
    note = tmp_path / "source.md"
    note.write_text("# Source\n\nExact assigned content.\n")
    import_local_document(project, note, kind="markdown")
    token_path = capability_paths(document["project_id"])["agent"]
    token = json.loads(token_path.read_text())["token"]
    run = RunRepository.create(project, controller="sol")
    source = SourceRepository(project).all()[0]
    task = run.dispatch(
        role="reader", subquestion="What is assigned?", evidence_strategy="exact passage",
        selected_context={}, allowed_capabilities=["read_source"],
        artifact_references=[f"source:{source['source_id']}"],
        reserve_deep_sources=1, reserve_provider_usage=1.0, controller_token=token,
    )
    bundle_path = tmp_path / "worker-bundle.json"
    bundled = _cli(
        env, "run", str(project), "bundle-task", "--run-id", run.run_id,
        "--task-id", task["task_id"], "--output", str(bundle_path),
        "--controller-token-file", str(token_path),
    )
    assert bundled.returncode == 0 and bundle_path.is_file()
    bundle = json.loads(bundle_path.read_text())
    assert json.loads(bundled.stdout)["bundle_id"] == bundle["bundle_id"]
    assert "ctl_" not in bundle_path.read_text() and str(project) not in bundle_path.read_text()

    result = bundle["result_template"]
    packet = tmp_path / "worker-result.json"
    packet.write_text(json.dumps(result))
    validated = _cli(
        env, "tools", "validate-result", "--file", str(packet),
        "--bundle", str(bundle_path),
    )
    receipt = json.loads(validated.stdout)
    assert validated.returncode == 0 and receipt["broker_admission"] is False
    assert receipt["bundle_id"] == bundle["bundle_id"]

    result["completed_at"] = "2999-01-01T00:00:00Z"
    packet.write_text(json.dumps(result))
    rejected = _cli(
        env, "tools", "validate-result", "--file", str(packet),
        "--bundle", str(bundle_path),
    )
    assert rejected.returncode == 2 and "later than receipt" in json.loads(rejected.stderr)["error"]


def test_every_grouped_tool_action_matches_argparse_and_declared_required_inputs() -> None:
    replacements = {"{PROJECT}": "/tmp/project", "{JSON}": "/tmp/input.json", "{TOKEN}": "/tmp/human.capability.json"}
    parser = _parser()
    action_count = 0
    for tool in tool_catalog()["tools"]:
        for action in tool.get("actions", []):
            action_count += 1
            argv = [replacements.get(value, value) for value in action["argv"]]
            parsed = parser.parse_args(argv)
            assert parsed.command == tool["command"].split()[1]
            if hasattr(parsed, "action"):
                assert parsed.action == action["name"]
            for input_spec in action["inputs"]:
                assert hasattr(parsed, input_spec["name"]), (tool["id"], action["name"], input_spec["name"])
                value = getattr(parsed, input_spec["name"])
                if input_spec["required"]:
                    assert value is not None and value != "" and value != [], (tool["id"], action["name"], input_spec["name"])
    assert action_count == 39
    scaffold = next(item for item in tool_catalog()["tools"] if item["id"] == "core.scaffold")
    assert scaffold["authority"] == "human_controller"


def _subparser(parser: argparse.ArgumentParser, dest: str, choice: str) -> argparse.ArgumentParser:
    action = next(
        item for item in parser._actions
        if isinstance(item, argparse._SubParsersAction) and item.dest == dest
    )
    return action.choices[choice]


def _parser_signature(action: argparse.Action) -> str:
    if isinstance(action, argparse._StoreTrueAction):
        value_type = "boolean"
    elif action.choices is not None:
        value_type = "enum"
    elif action.type is Path:
        value_type = "path"
    elif action.type is int:
        value_type = "integer"
    elif action.type is float:
        value_type = "number"
    else:
        value_type = "string"
    kind = "option" if action.option_strings else "positional"
    required = action.required if action.option_strings else action.nargs not in ("?", "*")
    repeatable = isinstance(action, argparse._AppendAction)
    default = json.dumps(action.default, ensure_ascii=False, separators=(",", ":"))
    choices = "-" if action.choices is None else ",".join(str(value) for value in action.choices)
    return "|".join((
        action.dest,
        value_type,
        kind,
        "required" if required else "optional",
        "repeatable" if repeatable else "single",
        default,
        choices,
    ))


def test_action_parser_signatures_have_complete_reverse_argparse_parity() -> None:
    parser = _parser()
    ignored_internal = {"help", "command", "action"}
    checked = 0
    for tool in tool_catalog()["tools"]:
        command = tool["command"].split()[1]
        command_parser = _subparser(parser, "command", command)
        parent_actions = [
            action for action in command_parser._actions
            if action.dest not in ignored_internal and not isinstance(action, argparse._SubParsersAction)
        ]
        for action_contract in tool.get("actions", []):
            if command in {"import", "init"}:
                parser_actions = parent_actions
            else:
                action_parser = _subparser(command_parser, "action", action_contract["name"])
                parser_actions = parent_actions + [
                    action for action in action_parser._actions
                    if action.dest not in ignored_internal and not isinstance(action, argparse._SubParsersAction)
                ]
            actual = [_parser_signature(action) for action in parser_actions]
            assert action_contract["parser_signature"] == actual, (tool["id"], action_contract["name"])
            assert [item["name"] for item in action_contract["inputs"]] == [item.split("|", 1)[0] for item in actual]
            assert [item["option_strings"] for item in action_contract["inputs"]] == [
                list(action.option_strings) for action in parser_actions
            ]
            for input_contract, signature in zip(action_contract["inputs"], actual, strict=True):
                _, value_type, kind, required, repeatability, default_json, choices_text = signature.split("|", 6)
                assert input_contract == {
                    "name": input_contract["name"],
                    "required": required == "required",
                    "type": value_type,
                    "secret": input_contract["name"] == "controller_token_file",
                    "positional": kind == "positional",
                    "repeatable": repeatability == "repeatable",
                    "choices": [] if choices_text == "-" else choices_text.split(","),
                    "default": json.loads(default_json),
                    "option_strings": list(parser_actions[action_contract["inputs"].index(input_contract)].option_strings),
                }
            assert len({item.split("|", 1)[0] for item in actual}) == len(actual)
            checked += 1
    assert checked == 39


def test_discussion_turn_rejects_promoted_node_option_and_dirty_flag_requires_git_triple(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args([
            "discuss", str(tmp_path / "project"), "add",
            "--entity-type", "node", "--entity-id", "nod_example", "--content", "turn",
            "--promoted-node-id", "nod_promoted",
        ])
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    initialize_project(project)
    with pytest.raises(ProjectError, match="allow_dirty_baseline requires repository"):
        RunRepository.create(project, controller="human", allow_dirty_baseline=True)
    assert not list((project / "runs").iterdir())


def test_zotero_bundle_is_deterministic_review_only_and_refuses_overwrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    initialize_project(project)
    bib = b"@article{stable,\n  title = {Stable title}\n}\n"
    csl = b'[{"id":"stable","title":"Stable title","type":"article-journal"}]\n'
    (project / "references/references.bib").write_bytes(bib)
    (project / "references/references.csl.json").write_bytes(csl)
    first = tmp_path / "first"
    second = tmp_path / "second"
    one = prepare_zotero_bundle(project, first)
    two = prepare_zotero_bundle(project, second)
    assert one["manifest_sha256"] == two["manifest_sha256"]
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
    manifest = json.loads((first / "review-manifest.json").read_text())
    assert manifest["review_status"] == "pending_human_review"
    assert manifest["zotero_mutated"] is False and manifest["network_used"] is False
    assert manifest["csl_items"] == 1
    assert manifest["citation_analysis"]["identity_mismatches"] == {"bibtex_only": [], "csl_json_only": []}
    assert [item["path"] for item in manifest["files"]] == ["references.bib", "references.csl.json"]
    with pytest.raises(ProjectError, match="nonempty"):
        prepare_zotero_bundle(project, first)


def test_zotero_bundle_rejects_invalid_csl_and_symbolic_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    initialize_project(project)
    (project / "references/references.csl.json").write_text("{}\n")
    with pytest.raises(ProjectError, match="contain an array"):
        prepare_zotero_bundle(project, tmp_path / "bundle")
    (project / "references/references.csl.json").write_text("[]\n")
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    with pytest.raises(ProjectError, match="symbolic-link"):
        prepare_zotero_bundle(project, link)


def test_adapter_generation_is_deterministic_offline_and_never_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    document = initialize_project(project)
    paths = capability_paths(document["project_id"])
    human_token = json.loads(paths["human"].read_text())["token"]
    first = tmp_path / "first.staging"
    second = tmp_path / "second.staging"
    one = generate_adapter(first, harness="test-harness")
    two = generate_adapter(second, harness="test-harness")
    assert one["manifest_sha256"] == two["manifest_sha256"]
    assert {path.name: path.read_bytes() for path in first.iterdir()} == {
        path.name: path.read_bytes() for path in second.iterdir()
    }
    verified = verify_adapter(first)
    assert verified["ok"] is True and verified["tools"] == len(tool_catalog()["tools"])
    env = {**os.environ, "SOLERESEARCH_CONFIG_HOME": str(tmp_path / "config")}
    agent_rejected = _cli(
        env, "adapter", "approve", str(first), "--project", str(project), "--reason", "Reviewed",
        "--controller-token-file", str(paths["agent"]),
    )
    assert agent_rejected.returncode == 2 and "verified human" in json.loads(agent_rejected.stderr)["error"]
    other_project = tmp_path / "other-project"
    other = initialize_project(other_project)
    wrong_human = capability_paths(other["project_id"])["human"]
    wrong_project = _cli(
        env, "adapter", "approve", str(first), "--project", str(project), "--reason", "Reviewed",
        "--controller-token-file", str(wrong_human),
    )
    assert wrong_project.returncode == 2 and "invalid external controller" in json.loads(wrong_project.stderr)["error"]
    approved_process = _cli(
        env, "adapter", "approve", str(first), "--project", str(project),
        "--reason", "Reviewed generated commands and side effects",
        "--controller-token-file", str(paths["human"]),
    )
    assert approved_process.returncode == 0
    approved = json.loads(approved_process.stdout)
    assert approved["install_status"] == "not_installed"
    record = json.loads((first / "approval.json").read_text())
    assert record["contract_test_passed"] is True and record["install_status"] == "not_installed"
    assert record["project_id"] == document["project_id"]
    assert record["capability_subject"] == "human-controller"
    assert json.loads((first / "manifest.json").read_text())["install_status"] == "not_installed"
    assert verify_adapter(first)["ok"] is True
    record["schema_version"] = 2
    (first / "approval.json").write_text(json.dumps(record))
    with pytest.raises(SchemaError, match="unsupported adapter_approval schema_version"):
        verify_adapter(first)
    (first / "approval.json").write_text(json.dumps({**record, "schema_version": 1}))
    with pytest.raises(ProjectError, match="immutable approval"):
        approve_adapter(
            first,
            project_path=project,
            controller_token=human_token,
            reason="again",
        )


def test_adapter_rejects_unsafe_targets_tampering_and_unknown_versions(tmp_path: Path) -> None:
    nonempty = tmp_path / "nonempty"
    nonempty.mkdir()
    (nonempty / "keep.txt").write_text("user file")
    with pytest.raises(ProjectError, match="nonempty"):
        generate_adapter(nonempty, harness="test")
    link_target = tmp_path / "real"
    link_target.mkdir()
    link = tmp_path / "linked.staging"
    link.symlink_to(link_target, target_is_directory=True)
    with pytest.raises(ProjectError, match="symbolic-link"):
        generate_adapter(link, harness="test")
    staging = tmp_path / "adapter.staging"
    generate_adapter(staging, harness="test")
    (staging / "adapter.py").write_text("tampered\n")
    with pytest.raises(ProjectError, match="hash verification"):
        verify_adapter(staging)
    generate_adapter(tmp_path / "unknown.staging", harness="test")
    manifest_path = tmp_path / "unknown.staging/manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["schema_version"] = 2
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(SchemaError, match="unsupported adapter_manifest schema_version"):
        verify_adapter(tmp_path / "unknown.staging")


def test_coordinated_adapter_script_and_manifest_tamper_never_executes(tmp_path: Path) -> None:
    staging = tmp_path / "adapter.staging"
    generate_adapter(staging, harness="test")
    marker = tmp_path / "marker"
    malicious = f"from pathlib import Path\nPath({str(marker)!r}).write_text('executed')\n".encode()
    script = staging / "contract_test.py"
    script.write_bytes(malicious)
    manifest_path = staging / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    for item in manifest["files"]:
        if item["path"] == "contract_test.py":
            item["sha256"] = hashlib.sha256(malicious).hexdigest()
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ProjectError, match="deterministic generator contract"):
        verify_adapter(staging)
    assert not marker.exists()


def test_empty_staging_is_restored_when_publish_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "adapter.staging"
    destination.mkdir()
    real_replace = os.replace

    def fail_publish(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        source_path = Path(source)
        if Path(target) == destination and ".adapter-" in source_path.name:
            raise OSError("injected publish failure")
        real_replace(source, target)

    monkeypatch.setattr(os, "replace", fail_publish)
    with pytest.raises(OSError, match="injected"):
        generate_adapter(destination, harness="test")
    assert destination.is_dir() and list(destination.iterdir()) == []
    assert not list(tmp_path.glob(".adapter.staging.empty-backup-*"))


def test_zotero_manifest_reports_duplicates_and_cross_format_identity_mismatches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    initialize_project(project)
    (project / "references/references.bib").write_text(
        "@article{dup,doi={10.1/same},eprint={2401.01234}}\n"
        "@article{dup,doi={10.1/same},eprint={2401.01234}}\n"
        "@article{bibonly,doi={10.1/bib-only}}\n"
    )
    (project / "references/references.csl.json").write_text(json.dumps([
        {"id": "csl-a", "DOI": "10.1/same", "arXiv": "2401.01234"},
        {"id": "csl-a", "DOI": "10.1/same", "arXiv": "2401.01234"},
        {"id": "csl-only", "DOI": "10.1/csl-only"},
    ]))
    prepare_zotero_bundle(project, tmp_path / "bundle")
    analysis = json.loads((tmp_path / "bundle/review-manifest.json").read_text())["citation_analysis"]
    assert analysis["duplicates"] == {
        "bibtex_citation_keys": ["dup"],
        "bibtex_dois": ["10.1/same"],
        "bibtex_arxiv_ids": ["2401.01234"],
        "csl_citation_ids": ["csl-a"],
        "csl_dois": ["10.1/same"],
        "csl_arxiv_ids": ["2401.01234"],
    }
    assert analysis["identity_mismatches"] == {
        "bibtex_only": ["doi:10.1/bib-only"],
        "csl_json_only": ["doi:10.1/csl-only"],
    }


def test_plugin_minimal_context_forward_path_and_structured_diagnosis(tmp_path: Path) -> None:
    env = {**os.environ, "SOLERESEARCH_CONFIG_HOME": str(tmp_path / "config")}
    project = tmp_path / "project"
    initialized = _cli(env, "init", str(project), "--name", "Forward Test")
    assert initialized.returncode == 0
    init_result = json.loads(initialized.stdout)
    human_capability = init_result["controller_capability_paths"]["human"]

    inspected = subprocess.run(
        [sys.executable, str(ORCHESTRATE / "scripts/inspect_project.py"), str(project), "--executable", str(_console_script())],
        capture_output=True, text=True, check=False, timeout=30, env=env,
    )
    assert inspected.returncode == 0
    assert json.loads(inspected.stdout)["status_tool"]["tool"]["id"] == "core.status"
    scaffolded = _cli(
        env, "scaffold", str(project), "--question", "What evidence is needed?",
        "--controller-token-file", human_capability,
    )
    assert scaffolded.returncode == 0
    started = _cli(env, "run", str(project), "start", "--controller", "human")
    run_id = json.loads(started.stdout)["run_id"]
    helper_task = tmp_path / "task.json"
    helper_task.write_text(json.dumps({
        "role": "reader",
        "subquestion": "What should be inspected?",
        "evidence_strategy": "Return exact locators only",
        "selected_context": {"node_ids": [json.loads(scaffolded.stdout)["node_id"]]},
        "artifact_references": [],
        "capabilities": [],
        "domains": [],
    }))
    dispatched = subprocess.run(
        [
            sys.executable, str(ORCHESTRATE / "scripts/dispatch_worker.py"),
            str(project), run_id, str(helper_task), "--controller-token-file", human_capability,
            "--executable", str(_console_script()),
        ],
        capture_output=True, text=True, check=False, timeout=30, env=env,
    )
    assert dispatched.returncode == 0
    task_id = json.loads(dispatched.stdout)["task_id"]
    gated = subprocess.run(
        [sys.executable, str(ORCHESTRATE / "scripts/gate_snapshot.py"), str(project), run_id, "--executable", str(_console_script())],
        capture_output=True, text=True, check=False, timeout=30, env=env,
    )
    assert gated.returncode == 0 and json.loads(gated.stdout)["gate"]["gate"]["status"] == "busy"
    cancelled = _cli(
        env, "run", str(project), "cancel-task", "--run-id", run_id,
        "--task-id", task_id, "--reason", "Forward-test cleanup",
        "--controller-token-file", human_capability,
    )
    assert cancelled.returncode == 0
    exported = _cli(env, "export", str(project), str(tmp_path / "export"))
    assert exported.returncode == 0 and (tmp_path / "export/manifest.json").is_file()

    missing = subprocess.run(
        [sys.executable, str(ORCHESTRATE / "scripts/inspect_project.py"), str(tmp_path / "missing"), "--executable", str(_console_script())],
        capture_output=True, text=True, check=False, timeout=30, env=env,
    )
    diagnosis = json.loads(missing.stderr)
    assert missing.returncode == 2 and diagnosis["command"] == "doctor"
    assert "missing required file" in diagnosis["error"]


def test_zotero_skill_script_uses_cli_contract(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SOLERESEARCH_CONFIG_HOME", str(tmp_path / "config"))
    project = tmp_path / "project"
    initialize_project(project)
    completed = subprocess.run(
        [sys.executable, str(ZOTERO / "scripts/prepare_bundle.py"), str(project), str(tmp_path / "bundle"), "--executable", str(_console_script())],
        capture_output=True, text=True, check=False, timeout=30, env=os.environ.copy(),
    )
    result = json.loads(completed.stdout)
    assert completed.returncode == 0 and result["review_status"] == "pending_human_review"
    assert (tmp_path / "bundle/review-manifest.json").is_file()


def test_plugin_skills_are_concise_complete_and_contain_no_generated_credentials() -> None:
    files = sorted(PLUGIN.rglob("*"))
    text_files = [path for path in files if path.is_file()]
    contents = "\n".join(path.read_text(encoding="utf-8") for path in text_files)
    assert "[TODO:" not in contents and "ctl_" not in contents
    orchestrate_text = (ORCHESTRATE / "SKILL.md").read_text()
    assert len(orchestrate_text.splitlines()) < 120
    assert "agents` limit includes the orchestrator" in orchestrate_text
    operate = next(item for item in tool_catalog()["tools"] if item["id"] == "run.operate")
    assert any("including the orchestrator" in limit for limit in operate["limits"])
    assert "mutates a Zotero" in (ZOTERO / "SKILL.md").read_text()
    assert hashlib.sha256((PLUGIN / ".codex-plugin/plugin.json").read_bytes()).hexdigest()
