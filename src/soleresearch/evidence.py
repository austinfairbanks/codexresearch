from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from soleresearch.errors import ProjectError, SchemaError
from soleresearch.project import load_project, utc_now
from soleresearch.schemas import SCHEMA_VERSION, validate_document
from soleresearch.sources import SourceRepository, load_extraction
from soleresearch.storage import canonical_json, confined_project_path, read_jsonl, write_jsonl
from soleresearch.writing import guarded_mutation

MAX_EXCERPT_CHARS = 1500
MAX_PARAPHRASE_CHARS = 4000


def _normalized(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _validate_locator(source: dict[str, Any], extraction: dict[str, Any], locator: dict[str, Any], excerpt: str) -> dict[str, Any]:
    expected = {"type", "page", "section", "paragraph", "figure", "table", "timestamp", "start_char", "end_char", "label"}
    if set(locator) != expected:
        missing = sorted(expected - set(locator))
        extra = sorted(set(locator) - expected)
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise ProjectError("invalid locator fields: " + "; ".join(details))
    locator_type = locator["type"]
    exact_slice = False
    if locator_type in {"figure", "table"}:
        reference = locator[locator_type]
        matches = [
            item for item in extraction.get("artifacts", [])
            if item.get("type") == locator_type and item.get("label") == reference
        ]
        if len(matches) != 1:
            raise ProjectError(f"{locator_type} locator must resolve one unique typed label")
        artifact = matches[0]
        if locator["page"] != artifact["page"] or locator["section"] != artifact["section"]:
            raise ProjectError(f"{locator_type} locator must preserve its associated page/section")
        if any(locator[key] is not None for key in ({"paragraph", "timestamp", "figure", "table"} - {locator_type})):
            raise ProjectError(f"{locator_type} locator contains fields for another locator type")
        haystack = str(artifact["context"])
        exact_slice = True
    elif locator_type == "timestamp":
        matches = [item for item in extraction.get("timeline", []) if item.get("timestamp") == locator["timestamp"]]
        if len(matches) != 1:
            if not extraction.get("timeline"):
                raise ProjectError("timestamp locator requires a source with an inspected timeline")
            raise ProjectError("timestamp locator must resolve one unique transcript segment")
        segment = matches[0]
        if locator["page"] != segment["page"] or locator["section"] != segment["section"]:
            raise ProjectError("timestamp locator must preserve its associated page/section")
        if any(locator[key] is not None for key in ("paragraph", "figure", "table")):
            raise ProjectError("timestamp locator contains fields for another locator type")
        haystack = str(segment["context"])
        exact_slice = True
    elif extraction.get("format") == "pdf":
        if locator_type != "page" or not isinstance(locator["page"], int) or isinstance(locator["page"], bool):
            raise ProjectError("PDF evidence requires a one-based page, figure, or table locator")
        if any(locator[key] is not None for key in ("section", "paragraph", "figure", "table", "timestamp", "start_char", "end_char")):
            raise ProjectError("page locator contains fields for another locator type")
        pages = extraction.get("pages", [])
        page = locator["page"]
        if page < 1 or page > len(pages):
            raise ProjectError(f"PDF page locator out of range: {page}")
        haystack = str(pages[page - 1])
    elif locator_type == "page":
        raise ProjectError("page locators are valid only for PDF sources")
    elif locator_type == "section":
        if any(locator[key] is not None for key in ("page", "paragraph", "figure", "table", "timestamp", "start_char", "end_char")):
            raise ProjectError("section locator contains fields for another locator type")
        heading = locator["section"]
        matches = [item for item in extraction.get("sections", []) if item.get("heading") == heading]
        if not isinstance(heading, str) or not heading.strip() or len(matches) != 1:
            raise ProjectError(f"section locator must resolve exactly once: {heading}")
        haystack = str(matches[0].get("text", ""))
    elif locator_type == "paragraph":
        if any(locator[key] is not None for key in ("page", "section", "figure", "table", "timestamp", "start_char", "end_char")):
            raise ProjectError("paragraph locator contains fields for another locator type")
        paragraph = locator["paragraph"]
        paragraphs = extraction.get("paragraphs", [])
        if not isinstance(paragraph, int) or isinstance(paragraph, bool) or paragraph < 1 or paragraph > len(paragraphs):
            raise ProjectError("paragraph locator out of range")
        haystack = str(paragraphs[paragraph - 1])
    elif locator_type == "captured_passage":
        if any(locator[key] is not None for key in ("page", "section", "paragraph", "figure", "table", "timestamp")):
            raise ProjectError("captured-passage locator contains fields for another locator type")
        haystack = str(extraction.get("text", ""))
        exact_slice = True
    else:
        raise ProjectError(f"unsupported locator type: {locator_type}")
    if exact_slice:
        start, end = locator["start_char"], locator["end_char"]
        if not isinstance(start, int) or isinstance(start, bool) or not isinstance(end, int) or isinstance(end, bool) or start < 0 or end <= start or end > len(haystack):
            raise ProjectError(f"{locator_type} locator requires valid context offsets")
        if haystack[start:end] != excerpt:
            raise ProjectError(f"{locator_type} offsets must slice the excerpt exactly")
    elif _normalized(excerpt) not in _normalized(haystack):
        raise ProjectError("evidence excerpt does not occur at the exact inspected locator")
    return locator


def locate_excerpt(extraction: dict[str, Any], excerpt: str) -> dict[str, Any]:
    text = str(extraction.get("text", ""))
    start = text.find(excerpt)
    if start < 0:
        raise ProjectError("excerpt does not occur verbatim in inspected content")
    if text.find(excerpt, start + 1) >= 0:
        raise ProjectError("excerpt occurs more than once; choose an explicit page, section, or paragraph locator")
    return {
        "type": "captured_passage", "page": None, "section": None, "paragraph": None,
        "figure": None, "table": None, "timestamp": None,
        "start_char": start, "end_char": start + len(excerpt), "label": f"characters {start}-{start + len(excerpt)}",
    }


def locator(
    kind: str, *, page: int | None = None, section: str | None = None,
    paragraph: int | None = None, figure: str | None = None, table: str | None = None,
    timestamp: str | None = None, start_char: int | None = None, end_char: int | None = None,
    label: str = "",
) -> dict[str, Any]:
    return {
        "type": kind, "page": page, "section": section, "paragraph": paragraph,
        "figure": figure, "table": table, "timestamp": timestamp,
        "start_char": start_char, "end_char": end_char, "label": label,
    }


class EvidenceRepository:
    def __init__(self, project_path: Path) -> None:
        self.root = project_path.resolve()
        load_project(self.root)
        self.path = confined_project_path(self.root, "evidence/evidence.jsonl")

    def all(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path)

    @guarded_mutation
    def add(
        self,
        *,
        source_id: str,
        locator: dict[str, Any],
        excerpt: str,
        paraphrase: str,
        stance: str,
        actor_type: str,
        actor_id: str,
        method: str,
        run_id: str | None = None,
        task_id: str | None = None,
        source_hash: str | None = None,
        source_version: str | None = None,
        now: Callable[[], str] = utc_now,
    ) -> tuple[dict[str, Any], bool]:
        paraphrase = paraphrase.strip()
        if not excerpt.strip() or len(excerpt) > MAX_EXCERPT_CHARS:
            raise ProjectError(f"excerpt must contain 1-{MAX_EXCERPT_CHARS} characters")
        if not paraphrase or len(paraphrase) > MAX_PARAPHRASE_CHARS:
            raise ProjectError(f"paraphrase must contain 1-{MAX_PARAPHRASE_CHARS} characters")
        source = SourceRepository(self.root).get(source_id)
        if not source["versions"]:
            raise ProjectError("discovery metadata cannot support evidence; inspect source content first")
        effective_hash = source_hash or source["content_hash"]
        effective_version = source_version or source["source_version"]
        versions = [
            item for item in source["versions"]
            if item["content_hash"] == effective_hash and item["source_version"] == effective_version
        ]
        if len(versions) != 1:
            raise ProjectError("requested source hash/version is not an inspected immutable version")
        version = versions[0]
        extraction = load_extraction(self.root, source_id, effective_hash, version["source_version"])
        exact_locator = _validate_locator(source, extraction, locator, excerpt)
        identity = canonical_json({
            "source_hash": effective_hash, "source_version": version["source_version"],
            "locator": exact_locator, "excerpt": excerpt,
        })
        evidence_id = "ev_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
        records = self.all()
        existing = next((item for item in records if item.get("evidence_id") == evidence_id), None)
        context = {
            "actor_type": actor_type, "actor_id": actor_id, "run_id": run_id,
            "task_id": task_id, "method": method,
        }
        if existing is not None:
            for attestation in existing["attestations"]:
                if all(attestation[key] == value for key, value in context.items()) and attestation["paraphrase"] == paraphrase and attestation["stance"] == stance:
                    return existing, False
            ordinal = 1 + sum(all(item[key] == value for key, value in context.items()) for item in existing["attestations"])
        else:
            ordinal = 1
        attestation_id = "att_" + hashlib.sha256(
            canonical_json({"evidence_id": evidence_id, **context, "ordinal": ordinal}).encode("utf-8")
        ).hexdigest()[:24]
        attestation = {
            "attestation_id": attestation_id, "paraphrase": paraphrase, "stance": stance,
            "run_id": run_id, "task_id": task_id, "actor_type": actor_type,
            "actor_id": actor_id, "method": method, "attested_at": now(),
        }
        record = {
            "schema_version": SCHEMA_VERSION,
            "evidence_id": evidence_id,
            "source_id": source["source_id"],
            "source_hash": effective_hash,
            "source_version": version["source_version"],
            "locator": exact_locator,
            "excerpt": excerpt,
            "retrieved_at": version["retrieved_at"],
            "attestations": [attestation] if existing is None else [*existing["attestations"], attestation],
        }
        try:
            validate_document("evidence", record)
        except SchemaError as exc:
            raise ProjectError(str(exc)) from exc
        evidence_created = existing is None
        if existing is None:
            records.append(record)
        else:
            records[records.index(existing)] = record
        write_jsonl(self.path, sorted(records, key=lambda item: item["evidence_id"]))
        return record, evidence_created
