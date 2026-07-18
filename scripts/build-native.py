#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a self-contained Sole Research executable")
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts"))
    parser.add_argument("--plugin", action="store_true", help="also install the executable into the local Codex plugin")
    parser.add_argument("--verify", action="store_true", help="run the isolated target-like verification after building")
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
        sbom = output / f"{artifact_name}.spdx.json"
        subprocess.run(
            [
                sys.executable,
                str(root / "scripts/generate-sbom.py"),
                "--lock", str(root / "uv.lock"),
                "--output", str(sbom),
                "--python-version", platform.python_version(),
            ],
            cwd=root,
            check=True,
        )
        build_metadata = {
            "schema_version": 1,
            "platform": system,
            "architecture": machine,
            "python": platform.python_version(),
            "packager": f"pyinstaller {importlib.metadata.version('pyinstaller')}",
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "sbom": sbom.name,
            "sbom_sha256": hashlib.sha256(sbom.read_bytes()).hexdigest(),
            "signed": "ad-hoc" if system == "macos" else "unsigned",
            "verified_commands": [
                "--version", "workspace select/show", "site show/configure",
                "doctor PROJECT", "publication status PROJECT", "mcp initialize/tools-list",
            ],
        }
        manifest = output / f"{artifact_name}.build.json"
        manifest.write_text(json.dumps(build_metadata, indent=2) + "\n", encoding="utf-8")
        if args.verify:
            subprocess.run(
                [sys.executable, str(root / "scripts/verify-native.py"), str(destination), "--build-metadata", str(manifest)],
                cwd=root,
                check=True,
            )
        if args.plugin:
            plugin_binary = root / "integrations/codex/soleresearch/bin" / f"sole-research{suffix}"
            plugin_binary.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(built, plugin_binary)
            shutil.copy2(sbom, plugin_binary.parent / "SBOM.spdx.json")
            plugin_metadata = {**build_metadata, "sbom": "SBOM.spdx.json"}
            (plugin_binary.parent / "BUILD.json").write_text(json.dumps(plugin_metadata, indent=2) + "\n", encoding="utf-8")
    print(destination)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
