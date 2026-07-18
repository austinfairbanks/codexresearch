#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a self-contained Sole Research executable")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--plugin", action="store_true", help="also install the executable into the local Codex plugin")
    args = parser.parse_args()

    root = Path(__file__).resolve().parent.parent
    system = {"darwin": "macos"}.get(platform.system().lower(), platform.system().lower())
    machine = platform.machine().lower().replace("x86_64", "x64").replace("amd64", "x64").replace("aarch64", "arm64")
    suffix = ".exe" if system == "windows" else ""
    artifact_name = f"sole-research-{system}-{machine}{suffix}"
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="soleresearch-pyinstaller-") as temporary:
        temp = Path(temporary)
        environment = os.environ.copy()
        environment["PYINSTALLER_CONFIG_DIR"] = str(temp / "cache")
        subprocess.run(
            [
                "uv", "run", "pyinstaller", "--noconfirm", "--clean", "--onefile",
                "--name", "sole-research", "--collect-data", "soleresearch",
                "--distpath", str(temp / "dist"), "--workpath", str(temp / "work"),
                "--specpath", str(temp / "spec"), str(root / "src/soleresearch/__main__.py"),
            ],
            cwd=root,
            env=environment,
            check=True,
        )
        built = temp / "dist" / f"sole-research{suffix}"
        destination = output / artifact_name
        shutil.copy2(built, destination)
        if args.plugin:
            plugin_binary = root / "integrations/codex/soleresearch/bin" / f"sole-research{suffix}"
            plugin_binary.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(built, plugin_binary)
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
