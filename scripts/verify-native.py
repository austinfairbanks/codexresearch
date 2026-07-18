#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def _run(executable: Path, arguments: list[str], *, cwd: Path, env: dict[str, str], stdin: str | None = None) -> dict[str, Any]:
    completed = subprocess.run(
        [str(executable), *arguments],
        cwd=cwd,
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(f"native verification failed for {' '.join(arguments)}: {completed.stderr[:1000]}")
    return json.loads(completed.stdout) if completed.stdout.strip().startswith("{") else {"stdout": completed.stdout.strip()}


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the standalone runtime in an isolated target-like environment")
    parser.add_argument("artifact", type=Path)
    parser.add_argument("--build-metadata", type=Path)
    args = parser.parse_args()
    artifact = args.artifact.resolve()
    metadata_path = args.build_metadata or artifact.with_name(artifact.name + ".build.json")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    actual_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    if actual_hash != metadata["sha256"]:
        raise SystemExit("native artifact checksum does not match build metadata")

    with tempfile.TemporaryDirectory(prefix="soleresearch-native-verify-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        workspace.mkdir()
        env = {
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "SOLERESEARCH_CONFIG_HOME": str(root / "config"),
        }
        if os.name == "nt" and os.environ.get("SystemRoot"):
            env["SystemRoot"] = os.environ["SystemRoot"]
        _run(artifact, ["--version"], cwd=root, env=env)
        _run(artifact, ["workspace", "select", str(workspace)], cwd=root, env=env)
        project = workspace / "project"
        initialized = _run(artifact, ["init", str(project), "--name", "Native verification"], cwd=root, env=env)
        _run(artifact, ["doctor", str(project)], cwd=root, env=env)
        _run(
            artifact,
            [
                "scaffold", str(project), "--question", "Does the standalone runtime work?",
                "--controller-token-file", initialized["controller_capability_paths"]["human"],
            ],
            cwd=root,
            env=env,
        )
        projection = root / "projection.json"
        _run(
            artifact,
            ["projection", str(project), "--published-revision", "1", "--output", str(projection)],
            cwd=root,
            env=env,
        )
        request = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}) + "\n"
        mcp = _run(artifact, ["mcp"], cwd=root, env=env, stdin=request)
        if len(mcp.get("result", {}).get("tools", [])) < 50:
            raise SystemExit("native MCP tool discovery is incomplete")
        if not projection.is_file():
            raise SystemExit("native projection was not written")
        print(json.dumps({
            "ok": True,
            "artifact": str(artifact),
            "sha256": actual_hash,
            "project_id": initialized["project_id"],
            "mcp_tool_count": len(mcp["result"]["tools"]),
            "target_runtime_dependencies": [],
        }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
