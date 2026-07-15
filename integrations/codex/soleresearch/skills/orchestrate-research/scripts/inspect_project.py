from __future__ import annotations

import argparse
import json
import subprocess
import sys


def _run(executable: str, *arguments: str) -> dict[str, object]:
    completed = subprocess.run(
        [executable, *arguments], capture_output=True, text=True, check=False, timeout=30
    )
    stream = completed.stdout if completed.returncode == 0 else completed.stderr
    try:
        value = json.loads(stream)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{arguments[0]} returned non-JSON output") from exc
    if completed.returncode != 0:
        print(json.dumps(value, indent=2, sort_keys=True), file=sys.stderr)
        raise SystemExit(completed.returncode)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise SystemExit(f"{arguments[0]} returned an unsupported schema")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect one Soleresearch project without mutation")
    parser.add_argument("project")
    parser.add_argument("--executable", default="sole-research")
    args = parser.parse_args()
    result = {
        "schema_version": 1,
        "doctor": _run(args.executable, "doctor", args.project),
        "status": _run(args.executable, "status", args.project),
        "status_tool": _run(args.executable, "tools", "show", "--tool-id", "core.status"),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
