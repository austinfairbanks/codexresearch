from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from typing import Any

import bibtexparser

from soleresearch.controller import require_controller
from soleresearch.errors import ProjectError
from soleresearch.project import load_project, utc_now
from soleresearch.schemas import SCHEMA_VERSION, tool_catalog, validate_document
from soleresearch.storage import atomic_write_json, canonical_json, read_json

ADAPTER_FILES = ("adapter.py", "contract_test.py", "tools.json")
ADAPTER_OPTIONAL_FILES = ("approval.json",)


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _manifest_sha256(path: Path) -> str:
    return _sha256(path.read_bytes())


def _new_directory_target(path: Path, *, label: str) -> tuple[Path, bool]:
    """Validate an explicit absent/empty directory without following a target link."""
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ProjectError(f"{label} refuses symbolic-link target: {expanded}")
    resolved = expanded.resolve()
    reserved = {Path("/").resolve(), Path.home().resolve(), Path.cwd().resolve()}
    if resolved in reserved:
        raise ProjectError(f"{label} target is a reserved directory: {resolved}")
    existed_empty = False
    if expanded.exists():
        if not expanded.is_dir():
            raise ProjectError(f"{label} target must be a directory: {expanded}")
        try:
            next(expanded.iterdir())
        except StopIteration:
            existed_empty = True
        else:
            raise ProjectError(f"{label} refuses nonempty target: {expanded}")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved, existed_empty


def _publish_directory(temporary: Path, destination: Path, existed_empty: bool) -> None:
    backup: Path | None = None
    try:
        if existed_empty:
            backup = destination.with_name(f".{destination.name}.empty-backup-{uuid.uuid4().hex}")
            os.replace(destination, backup)
            _fsync_directory(destination.parent)
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
        if backup is not None:
            try:
                backup.rmdir()
            except OSError:
                # The published destination is complete; retain an empty backup
                # rather than turning cleanup failure into a false rollback.
                pass
            _fsync_directory(destination.parent)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        if backup is not None and backup.exists() and not destination.exists():
            os.replace(backup, destination)
            _fsync_directory(destination.parent)
        raise


def _fsync_directory(path: Path) -> None:
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


def _duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def _write_durable_bytes(path: Path, content: bytes) -> None:
    with path.open("wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _doi(value: Any) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    normalized = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value.strip(), flags=re.I)
    return normalized.lower()


def _arxiv(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.search(r"(?:arxiv:\s*|arxiv\.org/(?:abs|pdf)/)?(\d{4}\.\d{4,5}(?:v\d+)?)", value, flags=re.I)
    return match.group(1).lower() if match else None


def _citation_analysis(bib_text: str, csl_items: list[dict[str, Any]]) -> dict[str, Any]:
    try:
        bib_entries = bibtexparser.loads(bib_text).entries
    except Exception as exc:  # bibtexparser exposes multiple parser exception types.
        raise ProjectError(f"invalid references.bib: {exc}") from exc
    bib_keys = [str(item.get("ID", "")).strip() for item in bib_entries if str(item.get("ID", "")).strip()]
    bib_dois = [value for item in bib_entries if (value := _doi(item.get("doi"))) is not None]
    bib_arxiv = [
        value for item in bib_entries
        if (value := _arxiv(item.get("eprint") or item.get("url"))) is not None
    ]
    csl_keys = [str(item.get("id", "")).strip() for item in csl_items if str(item.get("id", "")).strip()]
    csl_dois = [value for item in csl_items if (value := _doi(item.get("DOI") or item.get("doi"))) is not None]
    csl_arxiv = [
        value for item in csl_items
        if (value := _arxiv(item.get("arXiv") or item.get("arxiv") or item.get("URL"))) is not None
    ]

    def identities(items: list[dict[str, Any]], *, csl: bool) -> set[str]:
        result: set[str] = set()
        for item in items:
            doi = _doi(item.get("DOI") or item.get("doi"))
            arxiv = _arxiv(
                item.get("arXiv") or item.get("arxiv") or item.get("eprint")
                or item.get("URL") or item.get("url")
            )
            key = str(item.get("id" if csl else "ID", "")).strip().lower()
            result.add(f"doi:{doi}" if doi else f"arxiv:{arxiv}" if arxiv else f"key:{key}")
        return {value for value in result if value != "key:"}

    bib_identities = identities(bib_entries, csl=False)
    csl_identities = identities(csl_items, csl=True)
    return {
        "bibtex_entries": len(bib_entries),
        "csl_items": len(csl_items),
        "duplicates": {
            "bibtex_citation_keys": _duplicates(bib_keys),
            "bibtex_dois": _duplicates(bib_dois),
            "bibtex_arxiv_ids": _duplicates(bib_arxiv),
            "csl_citation_ids": _duplicates(csl_keys),
            "csl_dois": _duplicates(csl_dois),
            "csl_arxiv_ids": _duplicates(csl_arxiv),
        },
        "identity_mismatches": {
            "bibtex_only": sorted(bib_identities - csl_identities),
            "csl_json_only": sorted(csl_identities - bib_identities),
        },
    }


def prepare_zotero_bundle(project_path: Path, output_path: Path) -> dict[str, Any]:
    """Prepare reviewable open citation files without contacting or mutating Zotero."""
    project = load_project(project_path)
    destination, existed_empty = _new_directory_target(output_path, label="Zotero bundle")
    bib_path = project_path.resolve() / "references/references.bib"
    csl_path = project_path.resolve() / "references/references.csl.json"
    bib = bib_path.read_bytes()
    csl = csl_path.read_bytes()
    try:
        bib_text = bib.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProjectError(f"invalid UTF-8 in references.bib: {exc}") from exc
    try:
        csl_items = json.loads(csl.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProjectError(f"invalid references.csl.json: {exc}") from exc
    if not isinstance(csl_items, list) or any(not isinstance(item, dict) for item in csl_items):
        raise ProjectError("references.csl.json must contain an array of objects")
    citation_analysis = _citation_analysis(bib_text, csl_items)
    copied = (("references.bib", bib), ("references.csl.json", csl))
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "project_id": project["project_id"],
        "review_status": "pending_human_review",
        "zotero_mutated": False,
        "network_used": False,
        "files": [
            {"path": name, "sha256": _sha256(content), "bytes": len(content)}
            for name, content in copied
        ],
        "csl_items": len(csl_items),
        "citation_analysis": citation_analysis,
        "review_checks": [
            "Review duplicate citation keys and identifiers.",
            "Review titles, authors, dates, and publication fields.",
            "Choose either BibTeX or CSL-JSON for the manual Zotero import.",
            "Confirm the destination collection before importing in Zotero.",
        ],
    }
    validate_document("zotero_bundle_manifest", manifest)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.zotero-", dir=destination.parent))
    try:
        for name, content in copied:
            _write_durable_bytes(temporary / name, content)
        atomic_write_json(temporary / "review-manifest.json", manifest)
        _publish_directory(temporary, destination, existed_empty)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "schema_version": SCHEMA_VERSION,
        "output": str(destination),
        "manifest_sha256": _manifest_sha256(destination / "review-manifest.json"),
        "csl_items": len(csl_items),
        "review_status": "pending_human_review",
    }


def _adapter_program() -> bytes:
    text = '''from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Generated Soleresearch tool-catalog adapter")
    parser.add_argument("action", choices=("list", "show"))
    parser.add_argument("tool_id", nargs="?")
    args = parser.parse_args()
    catalog = json.loads(Path(__file__).with_name("tools.json").read_text(encoding="utf-8"))
    if catalog.get("schema_version") != 1 or catalog.get("catalog_version") != 1:
        parser.error("unsupported Soleresearch tool catalog version")
    if args.action == "list":
        print(json.dumps(catalog, indent=2, sort_keys=True))
        return 0
    if not args.tool_id:
        parser.error("show requires tool_id")
    item = next((tool for tool in catalog["tools"] if tool["id"] == args.tool_id), None)
    if item is None:
        parser.error(f"unknown tool_id: {args.tool_id}")
    print(json.dumps(item, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''
    return text.encode("utf-8")


def _adapter_contract_program() -> bytes:
    text = '''from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path


root = Path(__file__).resolve().parent
manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
assert manifest["schema_version"] == 1 and manifest["generator_version"] == 1
for item in manifest["files"]:
    assert hashlib.sha256((root / item["path"]).read_bytes()).hexdigest() == item["sha256"]
catalog = json.loads((root / "tools.json").read_text(encoding="utf-8"))
tools = {item["id"]: item for item in catalog["tools"]}
preflight = next(action for action in tools["core.tools"]["actions"] if action["name"] == "validate-result")
submission = next(action for action in tools["run.operate"]["actions"] if action["name"] == "import-result")
assert preflight["argv"][:2] == ["tools", "validate-result"]
assert any("validate-result" in constraint for constraint in submission["constraints"])
listed = subprocess.run([sys.executable, str(root / "adapter.py"), "list"], check=True, capture_output=True, text=True)
assert json.loads(listed.stdout) == catalog
shown = subprocess.run([sys.executable, str(root / "adapter.py"), "show", catalog["tools"][0]["id"]], check=True, capture_output=True, text=True)
assert json.loads(shown.stdout) == catalog["tools"][0]
print(json.dumps({"schema_version": 1, "ok": True, "tools": len(catalog["tools"])}, sort_keys=True))
'''
    return text.encode("utf-8")


def generate_adapter(staging_path: Path, *, harness: str) -> dict[str, Any]:
    normalized = harness.strip().lower()
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", normalized) is None:
        raise ProjectError("adapter harness must be lower-case hyphen-case")
    destination, existed_empty = _new_directory_target(staging_path, label="adapter generator")
    generated, manifest = _expected_adapter(normalized)
    validate_document("adapter_manifest", manifest)
    temporary = Path(tempfile.mkdtemp(prefix=f".{destination.name}.adapter-", dir=destination.parent))
    try:
        for name, content in generated.items():
            _write_durable_bytes(temporary / name, content)
        atomic_write_json(temporary / "manifest.json", manifest)
        _publish_directory(temporary, destination, existed_empty)
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise
    return {
        "schema_version": SCHEMA_VERSION,
        "staging": str(destination),
        "manifest_sha256": _manifest_sha256(destination / "manifest.json"),
        "generated_files": len(generated),
        "install_status": "not_installed",
    }


def _expected_adapter(harness: str) -> tuple[dict[str, bytes], dict[str, Any]]:
    catalog = tool_catalog()
    generated = {
        "adapter.py": _adapter_program(),
        "contract_test.py": _adapter_contract_program(),
        "tools.json": (json.dumps(catalog, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    }
    manifest: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generator": "soleresearch",
        "generator_version": 1,
        "harness": harness,
        "tool_catalog_sha256": _sha256(canonical_json(catalog).encode("utf-8")),
        "files": [
            {"path": name, "sha256": _sha256(generated[name])}
            for name in sorted(generated)
        ],
        "install_status": "not_installed",
    }
    return generated, manifest


def test_adapter(staging_path: Path) -> dict[str, Any]:
    root = staging_path.expanduser()
    if root.is_symlink() or not root.is_dir():
        raise ProjectError(f"adapter staging directory is missing or symbolic: {root}")
    root = root.resolve()
    manifest = validate_document("adapter_manifest", read_json(root / "manifest.json"))
    expected_files, expected_manifest = _expected_adapter(manifest["harness"])
    if manifest != expected_manifest:
        raise ProjectError("adapter manifest differs from deterministic generator contract")
    known = {"manifest.json", *ADAPTER_FILES, *ADAPTER_OPTIONAL_FILES}
    unexpected = sorted(path.name for path in root.iterdir() if path.name not in known)
    if unexpected:
        raise ProjectError("adapter staging has unexpected files: " + ", ".join(unexpected))
    expected_paths = [item["path"] for item in manifest["files"]]
    if expected_paths != sorted(ADAPTER_FILES):
        raise ProjectError("adapter manifest file set does not match generator contract")
    for item in manifest["files"]:
        path = root / item["path"]
        if (
            path.is_symlink()
            or not path.is_file()
            or path.read_bytes() != expected_files[item["path"]]
            or _sha256(path.read_bytes()) != item["sha256"]
        ):
            raise ProjectError(f"generated adapter file failed hash verification: {item['path']}")
    catalog = json.loads((root / "tools.json").read_text(encoding="utf-8"))
    if canonical_json(catalog) != canonical_json(tool_catalog()):
        raise ProjectError("generated adapter tool catalog differs from packaged contract")
    approval_path = root / "approval.json"
    if approval_path.exists() or approval_path.is_symlink():
        if approval_path.is_symlink() or not approval_path.is_file():
            raise ProjectError("adapter approval record must be a regular file")
        approval = validate_document("adapter_approval", read_json(approval_path))
        if approval["manifest_sha256"] != _manifest_sha256(root / "manifest.json"):
            raise ProjectError("adapter approval does not match the reviewed manifest")
    return {
        "schema_version": SCHEMA_VERSION,
        "staging": str(root),
        "manifest_sha256": _manifest_sha256(root / "manifest.json"),
        "ok": True,
        "tools": len(catalog["tools"]),
    }


def approve_adapter(
    staging_path: Path,
    *,
    project_path: Path,
    controller_token: str | None,
    reason: str,
) -> dict[str, Any]:
    project = load_project(project_path)
    capability = require_controller(project["project_id"], controller_token)
    if capability.authority != "human_accepted":
        raise ProjectError("adapter approval requires a verified human controller capability")
    if not reason.strip():
        raise ProjectError("adapter approval requires a reason")
    result = test_adapter(staging_path)
    root = Path(result["staging"])
    approval_path = root / "approval.json"
    if approval_path.exists() or approval_path.is_symlink():
        raise ProjectError("adapter staging already has an immutable approval record")
    approval = {
        "schema_version": SCHEMA_VERSION,
        "record_type": "adapter_review_approval",
        "project_id": project["project_id"],
        "capability_subject": capability.subject,
        "capability_id": capability.capability_id,
        "approved_at": utc_now(),
        "reason": reason.strip(),
        "manifest_sha256": result["manifest_sha256"],
        "contract_test_passed": True,
        "install_status": "not_installed",
    }
    validate_document("adapter_approval", approval)
    atomic_write_json(approval_path, approval)
    return {
        "schema_version": SCHEMA_VERSION,
        "staging": str(root),
        "approval": str(approval_path),
        "manifest_sha256": result["manifest_sha256"],
        "install_status": "not_installed",
    }
