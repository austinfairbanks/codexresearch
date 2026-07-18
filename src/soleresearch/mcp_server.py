from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, Callable, TextIO

from soleresearch import __version__
from soleresearch.controller import config_home
from soleresearch.errors import ProjectError
from soleresearch.storage import atomic_write_json, read_json

MCP_PROTOCOL_VERSION = "2025-06-18"
SERVER_INSTRUCTIONS = (
    "Sole Research is local-first and files-first. Use one active writer thread per project. "
    "Canonical writes must use the named tools and required controller capability. The hosted "
    "dashboard is read-only; publication failure never rolls back local research."
)
READ_ONLY_COMMANDS = {"doctor", "status", "projection", "tools"}
COMPATIBILITY_ONLY = {"serve"}
WORKSPACE_CONFIG = "workspace.json"


@dataclass(frozen=True)
class CommandTool:
    name: str
    command_path: tuple[str, ...]
    parser: argparse.ArgumentParser
    arguments: tuple[argparse.Action, ...]
    argument_groups: tuple[tuple[argparse.Action, ...], ...]
    description: str
    input_schema: dict[str, Any]


def _json_type(action: argparse.Action) -> dict[str, Any]:
    if isinstance(action, (argparse._StoreTrueAction, argparse._StoreFalseAction)):
        schema: dict[str, Any] = {"type": "boolean"}
    elif action.type is int:
        schema = {"type": "integer"}
    elif action.type is float:
        schema = {"type": "number"}
    else:
        schema = {"type": "string"}
    if action.choices is not None:
        schema["enum"] = list(action.choices)
    if isinstance(action, argparse._AppendAction) or action.nargs in {"*", "+"}:
        schema = {"type": "array", "items": schema}
    return schema


def _tool_name(path: tuple[str, ...]) -> str:
    return "soleresearch_" + "_".join(part.replace("-", "_") for part in path)


def _leaf_tools(parser: argparse.ArgumentParser) -> list[CommandTool]:
    tools: list[CommandTool] = []

    def visit(
        current: argparse.ArgumentParser,
        path: tuple[str, ...],
        groups: tuple[tuple[argparse.Action, ...], ...],
    ) -> None:
        subcommands = next((item for item in current._actions if isinstance(item, argparse._SubParsersAction)), None)
        if subcommands is not None:
            for name, child in sorted(subcommands.choices.items()):
                if not path and name in {"hook", "mcp"}:
                    continue
                child_arguments = tuple(
                    item for item in child._actions
                    if item.dest != "help" and not isinstance(item, argparse._SubParsersAction)
                )
                visit(child, (*path, name), (*groups, child_arguments))
            return
        if not path:
            return
        arguments = tuple(item for group in groups for item in group)
        properties = {item.dest: _json_type(item) for item in arguments}
        required = [
            item.dest for item in arguments
            if (not item.option_strings and item.nargs not in {"?", "*"}) or bool(getattr(item, "required", False))
        ]
        description = current.description or current.format_usage().strip()
        if path[0] in COMPATIBILITY_ONLY:
            description += " Compatibility-only command: run it directly; MCP will not start a persistent server."
        tools.append(CommandTool(
            name=_tool_name(path),
            command_path=path,
            parser=current,
            arguments=arguments,
            argument_groups=groups,
            description=description,
            input_schema={
                "type": "object",
                "additionalProperties": False,
                "properties": properties,
                "required": required,
            },
        ))

    visit(parser, (), ())
    names = [tool.name for tool in tools]
    if len(names) != len(set(names)):
        raise ProjectError("MCP tool names are not unique")
    return tools


def _workspace_root() -> Path:
    configured = os.environ.get("SOLERESEARCH_WORKSPACE_ROOT")
    if configured:
        root = Path(configured).resolve()
    else:
        selected = selected_workspace_root()
        if selected is None:
            raise ProjectError("no MCP workspace selected; call soleresearch_workspace_select first")
        root = selected
    if not root.is_dir() or root.is_symlink():
        raise ProjectError("MCP workspace root must be a real directory")
    return root


def selected_workspace_root() -> Path | None:
    path = config_home() / WORKSPACE_CONFIG
    if not path.is_file() or path.is_symlink():
        return None
    value = read_json(path)
    root = value.get("workspace_root") if isinstance(value, dict) else None
    if not isinstance(root, str) or not root:
        raise ProjectError("invalid selected workspace configuration")
    resolved = Path(root).resolve()
    if not resolved.is_dir() or resolved.is_symlink():
        raise ProjectError("selected workspace root is unavailable or unsafe")
    return resolved


def select_workspace_root(path: Path) -> dict[str, Any]:
    resolved = path.resolve()
    if not resolved.is_dir() or resolved.is_symlink():
        raise ProjectError("workspace root must be an existing real directory")
    destination = config_home() / WORKSPACE_CONFIG
    atomic_write_json(destination, {"schema_version": 1, "workspace_root": str(resolved)})
    destination.chmod(0o600)
    return {"schema_version": 1, "workspace_root": str(resolved), "configuration": str(destination)}


def _confine(path: Path, *, root: Path, allow_missing: bool) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ProjectError(f"path is outside the selected workspace root: {path}") from exc
    if not allow_missing and (not resolved.exists() or resolved.is_symlink()):
        raise ProjectError(f"path must exist inside the selected workspace root: {path}")
    return resolved


def _normalize_path(tool: CommandTool, action: argparse.Action, value: str, arguments: dict[str, Any]) -> str:
    path = Path(value)
    if tool.command_path == ("workspace", "select") and action.dest == "path":
        resolved = path.resolve()
        if not resolved.is_dir() or resolved.is_symlink():
            raise ProjectError("workspace root must be an existing real directory")
        return str(resolved)
    root = _workspace_root()
    if action.dest in {"controller_token_file", "publisher_token_file", "sites_auth_token_file"}:
        resolved = path.resolve()
        if not resolved.is_file() or resolved.is_symlink():
            raise ProjectError("credential path must be a regular file")
        return str(resolved)
    allow_missing = tool.command_path[0] in {"init", "export", "zotero-bundle", "adapter"} or action.dest in {"output"}
    resolved = _confine(path, root=root, allow_missing=allow_missing)
    if action.dest == "project" and resolved.parent != root:
        raise ProjectError("project must be an immediate child of the selected workspace root")
    return str(resolved)


def _argv(tool: CommandTool, arguments: dict[str, Any]) -> list[str]:
    if tool.command_path[0] in COMPATIBILITY_ONLY:
        raise ProjectError("serve is a compatibility command and cannot be started by an MCP tool call")
    argv: list[str] = []
    for command_name, group in zip(tool.command_path, tool.argument_groups, strict=True):
        argv.append(command_name)
        for action in group:
            if action.dest not in arguments:
                continue
            value = arguments[action.dest]
            values = value if isinstance(value, list) else [value]
            normalized: list[str] = []
            for item in values:
                if action.type is Path:
                    normalized.append(_normalize_path(tool, action, str(item), arguments))
                elif isinstance(item, bool):
                    normalized.append("true" if item else "false")
                else:
                    normalized.append(str(item))
            if not action.option_strings:
                argv.extend(normalized)
            elif isinstance(action, argparse._StoreTrueAction):
                if value:
                    argv.append(action.option_strings[0])
            elif isinstance(action, argparse._StoreFalseAction):
                if not value:
                    argv.append(action.option_strings[0])
            elif isinstance(action, argparse._AppendAction):
                for item in normalized:
                    argv.extend([action.option_strings[0], item])
            else:
                argv.append(action.option_strings[0])
                argv.extend(normalized)
    if tool.command_path[0] == "import" and len(tool.command_path) == 1:
        kind = arguments.get("kind")
        if kind in {"bibtex", "csl-json", "pdf", "markdown", "outline"}:
            input_index = argv.index(str(arguments["input"]))
            argv[input_index] = str(_confine(Path(argv[input_index]), root=_workspace_root(), allow_missing=False))
    return argv


def _executable() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable]
    return [sys.executable, "-m", "soleresearch"]


def execute_tool(tool: CommandTool, arguments: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ProjectError("tool arguments must be an object")
    unknown = set(arguments) - set(tool.input_schema["properties"])
    missing = set(tool.input_schema["required"]) - set(arguments)
    if unknown or missing:
        raise ProjectError(f"invalid tool arguments; missing={sorted(missing)}, unknown={sorted(unknown)}")
    command = [*_executable(), *_argv(tool, arguments)]
    completed = subprocess.run(command, capture_output=True, text=True, check=False, env=os.environ.copy())
    stream = completed.stdout if completed.returncode == 0 else completed.stderr
    try:
        result = json.loads(stream)
    except json.JSONDecodeError:
        result = {"error": stream.strip() or f"command exited {completed.returncode}"}
    return {"ok": completed.returncode == 0, "exit_code": completed.returncode, "result": result}


def _write_message(output: TextIO, message: dict[str, Any]) -> None:
    output.write(json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n")
    output.flush()


def serve_mcp(parser_factory: Callable[[], argparse.ArgumentParser], *, input_stream: TextIO = sys.stdin, output_stream: TextIO = sys.stdout) -> int:
    tools = _leaf_tools(parser_factory())
    by_name = {tool.name: tool for tool in tools}
    for line in input_stream:
        if not line.strip():
            continue
        request: Any = None
        try:
            request = json.loads(line)
            request_id = request.get("id") if isinstance(request, dict) else None
            method = request.get("method") if isinstance(request, dict) else None
            if method == "notifications/initialized":
                continue
            if method == "initialize":
                result = {
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "soleresearch", "version": __version__},
                    "instructions": SERVER_INSTRUCTIONS,
                }
            elif method == "tools/list":
                result = {"tools": [
                    {
                        "name": tool.name,
                        "title": " ".join(part.title() for part in tool.command_path),
                        "description": tool.description,
                        "inputSchema": tool.input_schema,
                        "annotations": {
                            "readOnlyHint": tool.command_path[0] in READ_ONLY_COMMANDS,
                            "destructiveHint": False,
                            "idempotentHint": tool.command_path[0] in READ_ONLY_COMMANDS,
                            "openWorldHint": tool.command_path[0] in {"import", "publish"},
                        },
                    }
                    for tool in tools
                ]}
            elif method == "tools/call":
                params = request.get("params") or {}
                name = params.get("name")
                if name not in by_name:
                    raise ProjectError("unknown MCP tool")
                value = execute_tool(by_name[name], params.get("arguments") or {})
                text = json.dumps(value["result"], ensure_ascii=False, indent=2, sort_keys=True)
                result = {
                    "content": [{"type": "text", "text": text}],
                    "structuredContent": value["result"] if isinstance(value["result"], dict) else {"value": value["result"]},
                    "isError": not value["ok"],
                }
            elif method == "ping":
                result = {}
            else:
                if request_id is None:
                    continue
                _write_message(output_stream, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "method not found"}})
                continue
            if request_id is not None:
                _write_message(output_stream, {"jsonrpc": "2.0", "id": request_id, "result": result})
        except (ValueError, TypeError, OSError, ProjectError) as exc:
            request_id = request.get("id") if isinstance(request, dict) else None
            if request_id is not None:
                _write_message(output_stream, {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32602, "message": str(exc)}})
    return 0
