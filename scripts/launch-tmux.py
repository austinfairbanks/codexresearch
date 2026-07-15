#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Launch or identify one project-bound Soleresearch tmux session.")
    value.add_argument("project", type=Path)
    value.add_argument("--session", required=True)
    value.add_argument("--ui-port", type=int, default=8765)
    value.add_argument("--dry-run", action="store_true")
    return value


def run(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        separator = arguments.index("--")
    except ValueError:
        separator = len(arguments)
    command = arguments[separator + 1:] if separator < len(arguments) else []
    args = parser().parse_args(arguments[:separator])
    project = args.project.expanduser().resolve()
    if not (project / "project.json").is_file():
        raise SystemExit(f"not a Soleresearch project: {project}")
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", args.session) is None:
        raise SystemExit("session must be 1-64 safe characters")
    if not 1 <= args.ui_port <= 65535:
        raise SystemExit("ui-port must be between 1 and 65535")
    if not command:
        raise SystemExit("an orchestration command is required after --")
    create = ["tmux", "new-session", "-d", "-s", args.session, "-c", str(project), *command]
    marker = f"SOLERESEARCH_PROJECT={project}"
    command_marker = "SOLERESEARCH_COMMAND=" + json.dumps(command, ensure_ascii=False, separators=(",", ":"))
    guidance = f"SSH access: ssh -L {args.ui_port}:127.0.0.1:{args.ui_port} HOST, then open http://127.0.0.1:{args.ui_port}"
    if args.dry_run:
        print(shlex.join(create))
        print(shlex.join(["tmux", "set-environment", "-t", args.session, "SOLERESEARCH_PROJECT", str(project)]))
        print(shlex.join(["tmux", "set-environment", "-t", args.session, "SOLERESEARCH_COMMAND", command_marker.split("=", 1)[1]]))
        print(guidance)
        return 0
    if shutil.which("tmux") is None:
        raise SystemExit("tmux is required; install it with the platform package manager")
    exists = subprocess.run(["tmux", "has-session", "-t", f"={args.session}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if exists:
        observed = subprocess.run(["tmux", "show-environment", "-t", f"={args.session}", "SOLERESEARCH_PROJECT"], capture_output=True, text=True, check=False)
        if observed.returncode != 0 or observed.stdout.strip() != marker:
            raise SystemExit(f"tmux session name collision: {args.session} is not bound to {project}")
        observed_command = subprocess.run(["tmux", "show-environment", "-t", f"={args.session}", "SOLERESEARCH_COMMAND"], capture_output=True, text=True, check=False)
        if observed_command.returncode != 0 or observed_command.stdout.strip() != command_marker:
            raise SystemExit(f"tmux session command collision: {args.session} is running a different orchestration command")
        print(f"Existing session: tmux attach-session -t {shlex.quote(args.session)}")
        print(guidance)
        return 0
    subprocess.run(create, check=True)
    try:
        subprocess.run(["tmux", "set-environment", "-t", f"={args.session}", "SOLERESEARCH_PROJECT", str(project)], check=True)
        subprocess.run(["tmux", "set-environment", "-t", f"={args.session}", "SOLERESEARCH_COMMAND", command_marker.split("=", 1)[1]], check=True)
    except BaseException:
        subprocess.run(["tmux", "kill-session", "-t", f"={args.session}"], check=False)
        raise
    print(f"Started session: tmux attach-session -t {shlex.quote(args.session)}")
    print(guidance)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
