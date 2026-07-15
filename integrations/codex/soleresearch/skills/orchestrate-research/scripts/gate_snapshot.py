from __future__ import annotations

import argparse
import json
import subprocess
import sys


def _call(executable: str, command: list[str]) -> dict[str, object]:
    completed = subprocess.run(
        [executable, *command], capture_output=True, text=True, check=False, timeout=30
    )
    stream = completed.stdout if completed.returncode == 0 else completed.stderr
    try:
        value = json.loads(stream)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"command returned non-JSON: {' '.join(command)}") from exc
    if completed.returncode != 0:
        print(json.dumps(value, indent=2, sort_keys=True), file=sys.stderr)
        raise SystemExit(completed.returncode)
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise SystemExit("command returned an unsupported schema")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect a Soleresearch gate and budget")
    parser.add_argument("project")
    parser.add_argument("run_id")
    parser.add_argument("--executable", default="sole-research")
    args = parser.parse_args()
    result = {
        "schema_version": 1,
        "gate": _call(args.executable, ["gate", args.project, args.run_id, "show"]),
        "budget": _call(args.executable, ["budget", args.project, args.run_id, "show"]),
    }
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
