#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import re
import tomllib
from pathlib import Path
from typing import Any


def _spdx_id(name: str) -> str:
    return "SPDXRef-Package-" + re.sub(r"[^A-Za-z0-9.-]", "-", name)


def _dependencies(package: dict[str, Any]) -> set[str]:
    return {
        item["name"]
        for item in package.get("dependencies", [])
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate the deterministic native-runtime SPDX inventory")
    parser.add_argument("--lock", type=Path, default=Path("uv.lock"))
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--python-version", default=platform.python_version())
    args = parser.parse_args()

    lock_bytes = args.lock.read_bytes()
    lock = tomllib.loads(lock_bytes.decode("utf-8"))
    packages = {item["name"]: item for item in lock["package"]}
    root = packages["sole-research"]
    selected = {"sole-research"}
    pending = list(_dependencies(root))
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected.add(name)
        pending.extend(_dependencies(packages[name]) - selected)

    package_records = []
    for name in sorted(selected):
        item = packages[name]
        package_records.append({
            "SPDXID": _spdx_id(name),
            "name": name,
            "versionInfo": item["version"],
            "downloadLocation": "NOASSERTION" if name == "sole-research" else f"https://pypi.org/project/{name}/{item['version']}/",
            "filesAnalyzed": False,
            "licenseConcluded": "NOASSERTION",
            "licenseDeclared": "NOASSERTION",
            "copyrightText": "NOASSERTION",
            "externalRefs": [{
                "referenceCategory": "PACKAGE-MANAGER",
                "referenceType": "purl",
                "referenceLocator": f"pkg:pypi/{name}@{item['version']}",
            }],
        })
    package_records.append({
        "SPDXID": "SPDXRef-Package-Python",
        "name": "CPython",
        "versionInfo": args.python_version,
        "downloadLocation": f"https://www.python.org/downloads/release/python-{args.python_version.replace('.', '')}/",
        "filesAnalyzed": False,
        "licenseConcluded": "PSF-2.0",
        "licenseDeclared": "PSF-2.0",
        "copyrightText": "NOASSERTION",
        "externalRefs": [{
            "referenceCategory": "PACKAGE-MANAGER",
            "referenceType": "purl",
            "referenceLocator": f"pkg:generic/cpython@{args.python_version}",
        }],
    })

    relationships = [{
        "spdxElementId": "SPDXRef-DOCUMENT",
        "relationshipType": "DESCRIBES",
        "relatedSpdxElement": "SPDXRef-Package-sole-research",
    }, {
        "spdxElementId": "SPDXRef-Package-sole-research",
        "relationshipType": "DEPENDS_ON",
        "relatedSpdxElement": "SPDXRef-Package-Python",
    }]
    for name in sorted(selected):
        for dependency in sorted(_dependencies(packages[name]) & selected):
            relationships.append({
                "spdxElementId": _spdx_id(name),
                "relationshipType": "DEPENDS_ON",
                "relatedSpdxElement": _spdx_id(dependency),
            })

    identity = hashlib.sha256(lock_bytes + args.python_version.encode("ascii")).hexdigest()
    document = {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": "sole-research-native-runtime",
        "documentNamespace": f"https://soleresearch.local/spdx/{identity}",
        "creationInfo": {
            "created": "1970-01-01T00:00:00Z",
            "creators": ["Tool: soleresearch-generate-sbom"],
            "comment": "Deterministic runtime dependency closure from uv.lock; platform binary contents are verified separately by checksum.",
        },
        "packages": package_records,
        "relationships": relationships,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
