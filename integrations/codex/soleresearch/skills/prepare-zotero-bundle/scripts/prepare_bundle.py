from __future__ import annotations

import argparse
import json
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare a reviewed manual Zotero import bundle")
    parser.add_argument("project")
    parser.add_argument("output")
    parser.add_argument("--executable", default="sole-research")
    args = parser.parse_args()
    completed = subprocess.run(
        [args.executable, "zotero-bundle", args.project, args.output],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    stream = completed.stdout if completed.returncode == 0 else completed.stderr
    try:
        value = json.loads(stream)
    except json.JSONDecodeError as exc:
        raise SystemExit("sole-research returned non-JSON output") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise SystemExit("sole-research returned an unsupported schema")
    output = sys.stdout if completed.returncode == 0 else sys.stderr
    print(json.dumps(value, indent=2, sort_keys=True), file=output)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())
