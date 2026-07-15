from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

FIELDS = {
    "role", "subquestion", "evidence_strategy", "selected_context",
    "artifact_references", "capabilities", "domains", "parent_task_id",
    "reserve_deep_sources", "reserve_provider_usage", "lease_minutes",
}
REQUIRED = {"role", "subquestion", "evidence_strategy", "selected_context"}


def main() -> int:
    parser = argparse.ArgumentParser(description="Dispatch one strict Soleresearch worker task")
    parser.add_argument("project")
    parser.add_argument("run_id")
    parser.add_argument("task", type=Path)
    parser.add_argument("--controller-token-file", required=True)
    parser.add_argument("--executable", default="sole-research")
    args = parser.parse_args()
    try:
        task = json.loads(args.task.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read task helper JSON: {exc}") from exc
    if not isinstance(task, dict):
        raise SystemExit("task helper JSON must be an object")
    unknown = sorted(set(task) - FIELDS)
    missing = sorted(REQUIRED - set(task))
    if unknown or missing:
        raise SystemExit(f"task helper fields invalid; missing={missing}, unknown={unknown}")
    if not isinstance(task["selected_context"], dict):
        raise SystemExit("selected_context must be an object")
    for key in ("role", "subquestion", "evidence_strategy"):
        if not isinstance(task[key], str) or not task[key].strip():
            raise SystemExit(f"{key} must be a non-empty string")
    if "parent_task_id" in task and task["parent_task_id"] is not None and not isinstance(task["parent_task_id"], str):
        raise SystemExit("parent_task_id must be a string or null")
    if "reserve_deep_sources" in task and (
        not isinstance(task["reserve_deep_sources"], int)
        or isinstance(task["reserve_deep_sources"], bool)
        or task["reserve_deep_sources"] < 0
    ):
        raise SystemExit("reserve_deep_sources must be a non-negative integer")
    for key in ("reserve_provider_usage", "lease_minutes"):
        if key in task and (
            not isinstance(task[key], (int, float))
            or isinstance(task[key], bool)
            or task[key] < 0
            or (key == "lease_minutes" and task[key] == 0)
        ):
            raise SystemExit(f"{key} must be a valid non-negative number")
    command = [
        args.executable, "run", args.project, "dispatch", "--run-id", args.run_id,
        "--role", str(task["role"]), "--subquestion", str(task["subquestion"]),
        "--evidence-strategy", str(task["evidence_strategy"]),
        "--controller-token-file", args.controller_token_file,
    ]
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", suffix=".json") as context:
        json.dump(task["selected_context"], context, sort_keys=True)
        context.flush()
        command.extend(["--context", context.name])
        for key, option in (("artifact_references", "--artifact-ref"), ("capabilities", "--capability"), ("domains", "--domain")):
            values = task.get(key, [])
            if not isinstance(values, list) or any(not isinstance(item, str) for item in values):
                raise SystemExit(f"{key} must be an array of strings")
            for value in values:
                command.extend([option, value])
        optional = {
            "parent_task_id": "--parent-task-id",
            "reserve_deep_sources": "--reserve-deep-sources",
            "reserve_provider_usage": "--reserve-provider-usage",
            "lease_minutes": "--lease-minutes",
        }
        for key, option in optional.items():
            if key in task and task[key] is not None:
                command.extend([option, str(task[key])])
        completed = subprocess.run(command, capture_output=True, text=True, check=False, timeout=30)
    stream = completed.stdout if completed.returncode == 0 else completed.stderr
    try:
        result = json.loads(stream)
    except json.JSONDecodeError as exc:
        raise SystemExit("sole-research returned non-JSON output") from exc
    if not isinstance(result, dict) or result.get("schema_version") != 1:
        raise SystemExit("sole-research returned an unsupported schema")
    output = sys.stdout if completed.returncode == 0 else sys.stderr
    print(json.dumps(result, indent=2, sort_keys=True), file=output)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
