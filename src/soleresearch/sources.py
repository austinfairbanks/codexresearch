from __future__ import annotations

import hashlib
import html
import importlib.metadata
import json
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, unquote_plus, urlsplit, urlunsplit

import bibtexparser
import trafilatura
from bibtexparser.bibdatabase import BibDatabase
from bibtexparser.bwriter import BibTexWriter
from pypdf import PdfReader

from soleresearch.errors import ProjectError, SchemaError
from soleresearch.project import load_project, utc_now
from soleresearch.retrieval import BoundedRetriever, RetrievedDocument
from soleresearch.schemas import SCHEMA_VERSION, validate_document
from soleresearch.storage import atomic_write_bytes, atomic_write_json, atomic_write_text, canonical_json, confined_project_path, read_json, read_jsonl, write_jsonl
from soleresearch.writing import canonical_write_guard, guarded_mutation

SOURCE_STATES = ("discovered", "metadata_resolved", "content_inspected", "local_copy_retained")
READING_STATES = ("unread", "queued", "reading", "read", "skipped")
_STATE_RANK = {value: index for index, value in enumerate(SOURCE_STATES)}
_TRACKING_QUERY = {"fbclid", "gclid", "mc_cid", "mc_eid"}
MAX_PDF_PAGES = 1000
MAX_EXTRACTED_CHARS = 5_000_000


def sha256_bytes(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def normalize_doi(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    normalized = re.sub(r"^(?:doi:\s*|https?://(?:dx\.)?doi\.org/)", "", normalized, flags=re.IGNORECASE)
    normalized = unquote(normalized)
    normalized = normalized.strip()
    return normalized if re.match(r"^10\.\d{4,9}/\S+$", normalized, flags=re.IGNORECASE) else None


def _doi_key(value: str | None) -> str | None:
    normalized = normalize_doi(value)
    if normalized is None:
        return None
    return "".join(chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in normalized)


def normalize_arxiv(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    normalized = re.sub(r"^arxiv:\s*", "", normalized)
    normalized = re.sub(r"^https?://arxiv\.org/(?:abs|pdf)/", "", normalized)
    normalized = normalized.removesuffix(".pdf")
    version = re.search(r"v(\d+)$", normalized)
    if version and int(version.group(1)) < 1:
        return None
    base = re.sub(r"v\d+$", "", normalized)
    legacy = re.fullmatch(r"[a-z-]+(?:\.[a-z-]+)?/\d{7}", base)
    modern = re.fullmatch(r"(\d{2})(\d{2})\.\d{4,5}", base)
    modern_valid = False
    if modern and 1 <= int(modern.group(2)) <= 12:
        year_month = int(modern.group(1) + modern.group(2))
        serial_length = len(base.partition(".")[2])
        modern_valid = (704 <= year_month <= 1412 and serial_length == 4) or (
            year_month >= 1501 and serial_length == 5
        )
    if legacy or modern_valid:
        return base + (version.group(0) if version else "")
    return None


def _arxiv_parts(value: str | None) -> tuple[str | None, str | None, int | None]:
    normalized = normalize_arxiv(value)
    if normalized is None:
        return None, None, None
    match = re.search(r"v(\d+)$", normalized)
    return normalized, re.sub(r"v\d+$", "", normalized), int(match.group(1)) if match else None


def canonicalize_url(value: str | None) -> str | None:
    if value is None:
        return None
    parsed = urlsplit(value.strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    if parsed.username is not None or parsed.password is not None:
        return None
    host = parsed.hostname.lower().rstrip(".")
    if ":" in host:
        host = f"[{host}]"
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and not ((parsed.scheme.lower() == "http" and port == 80) or (parsed.scheme.lower() == "https" and port == 443)):
        host = f"{host}:{port}"
    kept_query: list[str] = []
    for component in parsed.query.split("&") if parsed.query else []:
        key = unquote_plus(component.partition("=")[0]).casefold()
        if key.startswith("utm_") or key in _TRACKING_QUERY:
            continue
        kept_query.append(component)
    return urlunsplit((parsed.scheme.lower(), host, parsed.path, "&".join(kept_query), ""))


def _stable_source_id(*, doi_key: str | None, arxiv_base: str | None, url: str | None, content_hash: str | None, citation_key: str | None) -> str:
    if doi_key:
        identity = f"doi:{doi_key}"
    elif arxiv_base:
        identity = f"arxiv:{arxiv_base}"
    elif url:
        identity = f"url:{url}"
    elif citation_key:
        identity = f"citation:{citation_key}"
    elif content_hash:
        identity = f"content:{content_hash}"
    else:
        raise ProjectError("source needs DOI, arXiv, URL, content, or an explicit immutable citation/item ID")
    return "src_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]


def _metadata_version(record: dict[str, Any]) -> str:
    payload = {
        "title": record["title"], "authors": record["authors"], "published": record["published"],
        "identifiers": record["identifiers"], "canonical_url": record["canonical_url"], "metadata": record["metadata"],
    }
    return "metadata:" + hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def default_quality() -> dict[str, str]:
    return {
        "authority": "unknown",
        "methodology_transparency": "unknown",
        "evidence_directness": "unknown",
        "relevance": "unknown",
        "publication_status": "unknown",
        "notes": "",
    }


def _empty_retrieval() -> dict[str, Any]:
    return {"retrieved_at": None, "final_url": None, "http_status": None, "content_type": None, "charset": None, "content_bytes": None}


def _engine(name: str, package: str, config: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "version": importlib.metadata.version(package), "config": config}


def _markdown_engine() -> dict[str, Any]:
    return {"name": "soleresearch-markdown", "version": "1", "config": {"max_chars": MAX_EXTRACTED_CHARS}}


def _source_version(content_hash: str, engine: dict[str, Any], charset: str | None) -> str:
    identity = canonical_json(
        {"content_hash": content_hash, "extraction_engine": engine, "http_charset": charset}
    )
    return "sv_" + hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _extraction_hash(extraction: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(extraction).encode("utf-8")).hexdigest()


def _version_filename(content_hash: str, source_version: str) -> str:
    return f"{content_hash.removeprefix('sha256:')}--{source_version.removeprefix('sv_')}.json"


def _extract_markdown(content: str) -> dict[str, Any]:
    if len(content) > MAX_EXTRACTED_CHARS:
        raise ProjectError(f"decoded document exceeds {MAX_EXTRACTED_CHARS} character limit")
    lines = content.splitlines()
    sections: list[dict[str, str]] = []
    current_heading = "Document"
    current: list[str] = []
    for line in lines:
        match = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if match:
            if current:
                sections.append({"heading": current_heading, "text": "\n".join(current).strip()})
            current_heading = match.group(1).strip()
            current = []
        else:
            current.append(line)
    if current or not sections:
        sections.append({"heading": current_heading, "text": "\n".join(current).strip()})
    paragraphs = [item.strip() for item in re.split(r"\n\s*\n", content) if item.strip()]
    artifacts, timeline = _structured_artifacts(content)
    return {
        "format": "markdown", "text": content, "pages": [], "sections": sections,
        "paragraphs": paragraphs, "artifacts": artifacts, "timeline": timeline,
    }


def _structured_artifacts(
    text: str, *, page: int | None = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    artifacts: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    section = "Document"
    for raw_line in text.splitlines():
        line = raw_line.strip()
        heading = re.match(r"^#{1,6}\s+(.+?)\s*$", line)
        if heading:
            section = heading.group(1).strip()
            continue
        artifact_match = re.match(
            r"^(Figure|Fig\.|Table)\s+([A-Za-z0-9.-]+)\s*[:.-]?\s*(.*)$",
            line,
            flags=re.IGNORECASE,
        )
        if artifact_match:
            artifact_type = "table" if artifact_match.group(1).casefold() == "table" else "figure"
            canonical_prefix = "Table" if artifact_type == "table" else "Figure"
            artifacts.append(
                {
                    "type": artifact_type,
                    "label": f"{canonical_prefix} {artifact_match.group(2)}",
                    "context": line,
                    "page": page,
                    "section": None if page is not None else section,
                }
            )
        timestamp_match = re.match(r"^\[?(\d{2}):(\d{2})(?::(\d{2}))?\]?\s+(.+)$", line)
        if timestamp_match and int(timestamp_match.group(2)) < 60 and int(timestamp_match.group(3) or 0) < 60:
            timestamp = f"{timestamp_match.group(1)}:{timestamp_match.group(2)}"
            if timestamp_match.group(3) is not None:
                timestamp += f":{timestamp_match.group(3)}"
            timeline.append(
                {
                    "timestamp": timestamp,
                    "context": timestamp_match.group(4),
                    "page": page,
                    "section": None if page is not None else section,
                }
            )
    return artifacts, timeline


def _extract_html(content: bytes, charset: str | None) -> tuple[str, dict[str, Any]]:
    encoding = charset or "utf-8"
    try:
        text = content.decode(encoding, errors="replace")
    except LookupError as exc:
        raise ProjectError(f"unsupported HTTP charset: {encoding}") from exc
    if len(text) > MAX_EXTRACTED_CHARS:
        raise ProjectError(f"decoded document exceeds {MAX_EXTRACTED_CHARS} character limit")
    extracted = trafilatura.extract(text, output_format="markdown", include_links=True, include_tables=True) or ""
    title_match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    title = html.unescape(re.sub(r"\s+", " ", title_match.group(1)).strip()) if title_match else ""
    payload = _extract_markdown(extracted)
    payload["format"] = "html"
    return title, payload


def _extract_pdf(content: bytes) -> tuple[str, dict[str, Any]]:
    try:
        reader = PdfReader(BytesIO(content))
        if len(reader.pages) > MAX_PDF_PAGES:
            raise ProjectError(f"PDF exceeds {MAX_PDF_PAGES} page limit")
        pages = []
        artifacts: list[dict[str, Any]] = []
        timeline: list[dict[str, Any]] = []
        total_chars = 0
        for page in reader.pages:
            page_text = (page.extract_text() or "").strip()
            total_chars += len(page_text)
            if total_chars > MAX_EXTRACTED_CHARS:
                raise ProjectError(f"PDF extraction exceeds {MAX_EXTRACTED_CHARS} character limit")
            pages.append(page_text)
            page_artifacts, page_timeline = _structured_artifacts(page_text, page=len(pages))
            artifacts.extend(page_artifacts)
            timeline.extend(page_timeline)
        metadata = reader.metadata or {}
    except Exception as exc:
        raise ProjectError(f"cannot inspect PDF: {exc}") from exc
    text = "\n\n".join(pages)
    title = str(metadata.get("/Title") or "").strip()
    return title, {
        "format": "pdf", "text": text, "pages": pages, "sections": [],
        "paragraphs": [item.strip() for item in re.split(r"\n\s*\n", text) if item.strip()],
        "artifacts": artifacts, "timeline": timeline,
    }


class SourceRepository:
    def __init__(self, project_path: Path) -> None:
        self.root = project_path.resolve()
        self.project = load_project(self.root)
        self.path = confined_project_path(self.root, "sources/sources.jsonl")

    def all(self) -> list[dict[str, Any]]:
        return read_jsonl(self.path)

    def get(self, source_id: str) -> dict[str, Any]:
        for record in self.all():
            if record.get("source_id") == source_id or source_id in record.get("aliases", []):
                return record
        raise ProjectError(f"unknown source_id: {source_id}")

    @staticmethod
    def _strong_tokens(record: dict[str, Any]) -> set[str]:
        tokens = {f"doi:{item}" for item in record["identifiers"].get("doi_keys", [])}
        tokens.update(f"arxiv:{item}" for item in record["identifiers"].get("arxiv_bases", []))
        tokens.update(f"url:{item}" for item in record.get("url_aliases", []))
        tokens.update(f"hash:{item['content_hash']}" for item in record.get("versions", []))
        return tokens

    @classmethod
    def _equivalent_indices(cls, records: list[dict[str, Any]], incoming: dict[str, Any]) -> list[int]:
        def compatible(left: set[str], right: set[str]) -> bool:
            for prefix in ("doi:", "arxiv:"):
                left_values = {item for item in left if item.startswith(prefix)}
                right_values = {item for item in right if item.startswith(prefix)}
                if left_values and right_values and not left_values & right_values:
                    return False
            return True

        tokens = cls._strong_tokens(incoming)
        matched: set[int] = set()
        changed = True
        while changed:
            changed = False
            for index, record in enumerate(records):
                if index in matched:
                    continue
                record_tokens = cls._strong_tokens(record)
                if tokens & record_tokens and compatible(tokens, record_tokens):
                    matched.add(index)
                    tokens.update(record_tokens)
                    changed = True
        incoming_citations = set(incoming.get("citation_keys", []))
        citation_candidates = [
            index for index, record in enumerate(records)
            if index not in matched and incoming_citations & set(record.get("citation_keys", []))
        ]
        compatible = [
            index for index in citation_candidates
            if (
                not tokens
                or not cls._strong_tokens(records[index])
                or (tokens & cls._strong_tokens(records[index]) and compatible(tokens, cls._strong_tokens(records[index])))
            )
        ]
        if not tokens and len(compatible) > 1:
            candidate_strong = [cls._strong_tokens(records[index]) for index in compatible]
            nonempty = [item for item in candidate_strong if item]
            if len(nonempty) > 1 and not set.intersection(*nonempty):
                raise ProjectError(
                    "citation/item key refers to multiple conflicting strong identities; explicit human resolution required"
                )
        matched.update(compatible)
        return sorted(matched)

    @staticmethod
    def _merge(participants: list[dict[str, Any]]) -> dict[str, Any]:
        doi_keys = sorted({item for record in participants for item in record["identifiers"]["doi_keys"]})
        arxiv_bases = sorted({item for record in participants for item in record["identifiers"]["arxiv_bases"]})
        urls = sorted({item for record in participants for item in record["url_aliases"]})
        versions_by_id: dict[str, dict[str, Any]] = {}
        for version in (version for record in participants for version in record["versions"]):
            existing_version = versions_by_id.get(version["source_version"])
            if existing_version is not None:
                stable_existing = {key: value for key, value in existing_version.items() if key != "retrieved_at"}
                stable_incoming = {key: value for key, value in version.items() if key != "retrieved_at"}
                if stable_existing != stable_incoming:
                    raise ProjectError(
                        f"conflicting extraction for immutable source_version {version['source_version']}"
                    )
                versions_by_id[version["source_version"]] = {
                    **existing_version,
                    "retrieved_at": min(existing_version["retrieved_at"], version["retrieved_at"]),
                }
            else:
                versions_by_id[version["source_version"]] = version
        versions = [versions_by_id[key] for key in sorted(versions_by_id)]
        current_candidates = [record for record in participants if record.get("content_hash")]
        current = max(
            current_candidates,
            key=lambda item: (item["retrieval"]["retrieved_at"] or "", item["content_hash"]),
            default=None,
        )
        metadata: dict[str, Any] = {}
        for record in sorted(participants, key=lambda item: item["source_id"]):
            metadata.update(record["metadata"])
        for collection in ("bibtex_records", "csl_records"):
            raw_records = {
                canonical_json(item): item
                for record in participants
                for item in record["metadata"].get(collection, [])
                if isinstance(item, dict)
            }
            if raw_records:
                metadata[collection] = [raw_records[key] for key in sorted(raw_records)]
        citation_keys = sorted({item for record in participants for item in record.get("citation_keys", [])})
        primary_doi = min(
            (record["identifiers"]["doi"] for record in participants if record["identifiers"]["doi"]),
            key=lambda value: (value.casefold(), value),
            default=None,
        )
        arxiv_values = [record["identifiers"]["arxiv"] for record in participants if record["identifiers"]["arxiv"]]
        primary_arxiv = max(
            arxiv_values,
            key=lambda value: (_arxiv_parts(value)[2] or 0, value),
            default=None,
        )
        canonical_id = _stable_source_id(
            doi_key=doi_keys[0] if doi_keys else None,
            arxiv_base=arxiv_bases[0] if arxiv_bases else None,
            url=urls[0] if urls else None,
            content_hash=versions[0]["content_hash"] if versions else None,
            citation_key=citation_keys[0] if citation_keys else None,
        )
        aliases = sorted(
            {
                alias
                for record in participants
                for alias in [record["source_id"], *record.get("aliases", [])]
                if alias != canonical_id
            }
        )
        base = min(participants, key=lambda item: item["source_id"])
        def latest_resolved(field: str) -> Any:
            candidates = [record for record in participants if record[field] not in (None, "", [], {})]
            if not candidates:
                return participants[0][field]
            selected = max(
                candidates,
                key=lambda record: (
                    record["metadata_updated_at"],
                    len(canonical_json(record[field])),
                    canonical_json(record[field]),
                ),
            )
            return selected[field]

        quality: dict[str, str] = {}
        for dimension in ("authority", "methodology_transparency", "evidence_directness", "relevance", "publication_status"):
            non_default = sorted({record["quality"][dimension] for record in participants if record["quality"][dimension] != "unknown"})
            if len(non_default) > 1:
                raise ProjectError(
                    f"conflicting source quality dimension {dimension}: {', '.join(non_default)}; human resolution required"
                )
            quality[dimension] = non_default[0] if non_default else "unknown"
        quality["notes"] = "\n\n".join(
            sorted({record["quality"]["notes"].strip() for record in participants if record["quality"]["notes"].strip()})
        )
        reading_rank = {"unread": 0, "queued": 1, "reading": 2, "read": 3, "skipped": 4}
        reading_values = {record["human_reading_state"] for record in participants}
        if {"read", "skipped"} <= reading_values:
            raise ProjectError("conflicting terminal reading states read/skipped; human resolution required")
        import_provenance_by_key = {
            canonical_json(item): item
            for record in participants
            for item in record["import_provenance"]
        }
        import_provenance = [import_provenance_by_key[key] for key in sorted(import_provenance_by_key)]
        result = dict(base)
        result.update(
            {
                "source_id": canonical_id,
                "aliases": aliases,
                "citation_keys": citation_keys,
                "identifiers": {
                    "doi": primary_doi,
                    "doi_key": doi_keys[0] if doi_keys else None,
                    "doi_keys": doi_keys,
                    "arxiv": primary_arxiv,
                    "arxiv_base": _arxiv_parts(primary_arxiv)[1] if primary_arxiv else None,
                    "arxiv_bases": arxiv_bases,
                    "arxiv_version": _arxiv_parts(primary_arxiv)[2] if primary_arxiv else None,
                },
                "canonical_url": urls[0] if urls else None,
                "url_aliases": urls,
                "versions": versions,
                "metadata": metadata,
                "title": latest_resolved("title"),
                "authors": latest_resolved("authors"),
                "published": latest_resolved("published"),
                "human_reading_state": max(
                    (record["human_reading_state"] for record in participants), key=reading_rank.__getitem__
                ),
                "quality": quality,
                "data_policy": "local_private" if any(record["data_policy"] == "local_private" for record in participants) else "public_only",
                "imported_at": min(record["imported_at"] for record in participants),
                "metadata_updated_at": max(record["metadata_updated_at"] for record in participants),
                "import_provenance": import_provenance,
            }
        )
        if current is not None:
            result.update(
                {
                    "content_hash": current["content_hash"],
                    "source_version": current["source_version"],
                    "retrieval": current["retrieval"],
                    "state": current["state"],
                    "local_copy_path": current["local_copy_path"],
                }
            )
        else:
            result["content_hash"] = None
            result["versions"] = []
            result["source_version"] = _metadata_version(result)
            result["state"] = max((record["state"] for record in participants), key=_STATE_RANK.__getitem__)
        return result

    @guarded_mutation
    def upsert(self, incoming: dict[str, Any], *, extraction: dict[str, Any] | None = None) -> tuple[dict[str, Any], bool]:
        try:
            validate_document("source", incoming)
        except SchemaError as exc:
            raise ProjectError(str(exc)) from exc
        records = self.all()
        matched = self._equivalent_indices(records, incoming)
        created = not matched
        staged_path: Path | None = None
        staged_created = False
        if extraction is not None:
            if not incoming.get("content_hash"):
                raise ProjectError("inspected extraction requires a content hash")
            relative = Path(".soleresearch/extractions") / incoming["source_id"] / _version_filename(
                incoming["content_hash"], incoming["source_version"]
            )
            staged_path = confined_project_path(self.root, relative)
            staged_created = not staged_path.exists()
            if not staged_created:
                existing_extraction = read_json(staged_path)
                if not isinstance(existing_extraction, dict) or _extraction_hash(existing_extraction) != incoming["versions"][0]["extraction_hash"]:
                    raise ProjectError(
                        f"conflicting extraction cache for immutable source_version {incoming['source_version']}"
                    )
            atomic_write_json(staged_path, extraction)
        try:
            participants = [records[index] for index in matched] + [incoming]
            record = self._merge(participants)
            validate_document("source", record)
            remaining = [item for index, item in enumerate(records) if index not in matched]
            remaining.append(record)
            write_jsonl(self.path, sorted(remaining, key=lambda item: item["source_id"]))
        except BaseException:
            if staged_created and staged_path is not None:
                staged_path.unlink(missing_ok=True)
            raise
        render_reading_queue(self.root)
        return record, created

    @guarded_mutation
    def set_reading_state(self, source_id: str, state: str) -> dict[str, Any]:
        if state not in READING_STATES:
            raise ProjectError(f"unsupported reading state: {state}")
        records = self.all()
        for record in records:
            if record.get("source_id") == source_id or source_id in record.get("aliases", []):
                record["human_reading_state"] = state
                write_jsonl(self.path, sorted(records, key=lambda item: item["source_id"]))
                render_reading_queue(self.root)
                return record
        raise ProjectError(f"unknown source_id: {source_id}")

    @guarded_mutation
    def set_quality(self, source_id: str, **dimensions: str) -> dict[str, Any]:
        allowed = set(default_quality())
        unknown = sorted(set(dimensions) - allowed)
        if unknown:
            raise ProjectError(f"unknown quality dimensions: {', '.join(unknown)}")
        records = self.all()
        for record in records:
            if record.get("source_id") != source_id and source_id not in record.get("aliases", []):
                continue
            quality = {**record["quality"], **dimensions}
            candidate = {**record, "quality": quality}
            try:
                validate_document("source", candidate)
            except SchemaError as exc:
                raise ProjectError(str(exc)) from exc
            records[records.index(record)] = candidate
            write_jsonl(self.path, sorted(records, key=lambda item: item["source_id"]))
            render_reading_queue(self.root)
            return candidate
        raise ProjectError(f"unknown source_id: {source_id}")


def _render_reading_queue(project_path: Path) -> list[dict[str, Any]]:
    root = project_path.resolve()
    records = read_jsonl(confined_project_path(root, "sources/sources.jsonl"))
    reading_rank = {"reading": 0, "queued": 1, "unread": 2, "read": 3, "skipped": 4}
    ordered = sorted(records, key=lambda item: (reading_rank[item["human_reading_state"]], -_STATE_RANK[item["state"]], item["title"].casefold(), item["source_id"]))
    lines = ["# Reading Queue", ""]
    for item in ordered:
        title = item["title"] or item["canonical_url"] or item["source_id"]
        dimensions = ", ".join(f"{key}={value}" for key, value in item["quality"].items() if key != "notes")
        lines.extend([
            f"- [{item['human_reading_state']}] **{title}** (`{item['source_id']}`)",
            f"  - source_state: `{item['state']}`; {dimensions}",
        ])
        if item["quality"]["notes"]:
            lines.append(f"  - quality_notes: {item['quality']['notes']}")
    atomic_write_text(confined_project_path(root, "sources/reading-queue.md"), "\n".join(lines) + "\n")
    return ordered


def render_reading_queue(project_path: Path) -> list[dict[str, Any]]:
    with canonical_write_guard(project_path):
        return _render_reading_queue(project_path)


def _base_record(
    *, source_type: str, title: str, authors: list[str], published: str | None,
    doi: str | None, arxiv: str | None, url: str | None, content_hash: str | None,
    state: str, data_policy: str, import_method: str, metadata: dict[str, Any],
    retrieval: dict[str, Any] | None = None, local_copy_path: str | None = None,
    extraction_engine: dict[str, Any] | None = None, extraction: dict[str, Any] | None = None,
    citation_key: str | None = None,
    now: Callable[[], str] = utc_now,
) -> dict[str, Any]:
    doi = normalize_doi(doi)
    doi_key = _doi_key(doi)
    arxiv, arxiv_base, arxiv_version = _arxiv_parts(arxiv)
    url = canonicalize_url(url)
    effective_retrieval = retrieval or _empty_retrieval()
    imported_at = now()
    versions = []
    if content_hash:
        if extraction_engine is None or extraction is None or effective_retrieval["retrieved_at"] is None:
            raise ProjectError("inspected content requires extraction engine and retrieval time")
        derived_version = _source_version(content_hash, extraction_engine, effective_retrieval["charset"])
        versions.append(
            {
                "content_hash": content_hash,
                "source_version": derived_version,
                "retrieved_at": effective_retrieval["retrieved_at"],
                "extraction_engine": extraction_engine,
                "http_charset": effective_retrieval["charset"],
                "extraction_hash": _extraction_hash(extraction),
            }
        )
    record: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "source_id": _stable_source_id(doi_key=doi_key, arxiv_base=arxiv_base, url=url, content_hash=content_hash, citation_key=citation_key),
        "aliases": [],
        "citation_keys": [citation_key] if citation_key else [],
        "source_type": source_type, "title": title.strip(), "authors": [item.strip() for item in authors if item.strip()],
        "published": published,
        "identifiers": {
            "doi": doi, "doi_key": doi_key, "doi_keys": [doi_key] if doi_key else [],
            "arxiv": arxiv, "arxiv_base": arxiv_base, "arxiv_bases": [arxiv_base] if arxiv_base else [],
            "arxiv_version": arxiv_version,
        },
        "canonical_url": url, "url_aliases": [url] if url else [],
        "content_hash": content_hash, "source_version": versions[0]["source_version"] if versions else "pending", "versions": versions, "state": state,
        "human_reading_state": "unread", "quality": default_quality(), "retrieval": effective_retrieval,
        "local_copy_path": local_copy_path, "metadata": metadata, "data_policy": data_policy,
        "imported_at": imported_at, "import_method": import_method,
        "metadata_updated_at": imported_at,
        "import_provenance": [{"imported_at": imported_at, "import_method": import_method, "source_type": source_type, "data_policy": data_policy}],
    }
    if record["source_version"] == "pending":
        record["source_version"] = _metadata_version(record)
    return record


def _policy(root: Path, private: bool) -> str:
    project = load_project(root)
    if private and project["data_policy"] != "local_private":
        raise ProjectError("private imports require a project with data_policy=local_private")
    return "local_private" if private else "public_only"


def _retain_copy(root: Path, source_id: str, suffix: str, content: bytes) -> str:
    relative = Path("source-copies") / f"{source_id}{suffix}"
    target = confined_project_path(root, relative)
    atomic_write_bytes(target, content)
    return relative.as_posix()


def import_url(
    project_path: Path, url: str, *, inspect: bool = False, retain_copy: bool = False,
    retriever: BoundedRetriever | None = None, now: Callable[[], str] = utc_now,
) -> tuple[dict[str, Any], bool]:
    root = project_path.resolve()
    requested_canonical = canonicalize_url(url)
    if requested_canonical is None:
        raise ProjectError(f"invalid HTTP/HTTPS URL: {url}")
    canonical = requested_canonical
    doi = normalize_doi(canonical)
    arxiv = normalize_arxiv(canonical)
    extraction = None
    content_hash = None
    title = ""
    state = "discovered"
    retrieval = _empty_retrieval()
    local_copy_path = None
    document: RetrievedDocument | None = None
    owned = retriever is None
    if inspect:
        retriever = retriever or BoundedRetriever()
        try:
            document = retriever.retrieve(canonical)
        finally:
            if owned:
                retriever.close()
        content_hash = sha256_bytes(document.content)
        canonical = canonicalize_url(document.final_url)
        doi = doi or normalize_doi(canonical)
        arxiv = arxiv or normalize_arxiv(canonical)
        if document.content_type in {"application/pdf", "application/x-pdf"} or document.content.startswith(b"%PDF"):
            title, extraction = _extract_pdf(document.content)
            extraction_engine = _engine(
                "pypdf", "pypdf", {"max_pages": MAX_PDF_PAGES, "max_chars": MAX_EXTRACTED_CHARS}
            )
            suffix = ".pdf"
        elif document.content_type in {"text/html", "application/xhtml+xml"}:
            title, extraction = _extract_html(document.content, document.charset)
            extraction_engine = _engine(
                "trafilatura", "trafilatura", {"output_format": "markdown", "include_links": True, "include_tables": True, "max_chars": MAX_EXTRACTED_CHARS}
            )
            suffix = ".html"
        elif document.content_type in {"text/plain", "text/markdown"}:
            encoding = document.charset or "utf-8"
            try:
                decoded = document.content.decode(encoding)
            except (LookupError, UnicodeDecodeError) as exc:
                raise ProjectError(f"cannot decode {document.content_type} document as {encoding}: {exc}") from exc
            extraction = _extract_markdown(decoded)
            extraction_engine = _markdown_engine()
            heading = re.search(r"^#\s+(.+)$", decoded, flags=re.MULTILINE)
            title = heading.group(1).strip() if heading else ""
            suffix = ".md" if document.content_type == "text/markdown" else ".txt"
        else:
            raise ProjectError(f"unsupported non-document media type: {document.content_type}")
        state = "content_inspected"
        retrieval = {"retrieved_at": now(), "final_url": canonical, "http_status": document.status_code, "content_type": document.content_type, "charset": document.charset, "content_bytes": len(document.content)}
    record = _base_record(source_type="url", title=title, authors=[], published=None, doi=doi, arxiv=arxiv, url=canonical, content_hash=content_hash, state=state, data_policy="public_only", import_method="url", metadata={"requested_url": requested_canonical}, retrieval=retrieval, extraction_engine=extraction_engine if extraction is not None else None, extraction=extraction, now=now)
    record["url_aliases"] = sorted({requested_canonical, *record["url_aliases"]})
    if document is not None and retain_copy:
        local_copy_path = _retain_copy(root, record["source_id"], suffix, document.content)
        record["local_copy_path"] = local_copy_path
        record["state"] = "local_copy_retained"
    return SourceRepository(root).upsert(record, extraction=extraction)


def import_identifier(project_path: Path, value: str, *, kind: str, now: Callable[[], str] = utc_now) -> tuple[dict[str, Any], bool]:
    if kind == "doi":
        doi = normalize_doi(value)
        if not doi:
            raise ProjectError(f"invalid DOI: {value}")
        arxiv = None
        url = f"https://doi.org/{doi}"
    elif kind == "arxiv":
        arxiv = normalize_arxiv(value)
        if not arxiv:
            raise ProjectError(f"invalid arXiv identifier: {value}")
        doi = None
        url = f"https://arxiv.org/abs/{arxiv}"
    else:
        raise ProjectError(f"unsupported identifier kind: {kind}")
    record = _base_record(source_type=kind, title="", authors=[], published=None, doi=doi, arxiv=arxiv, url=url, content_hash=None, state="discovered", data_policy="public_only", import_method=kind, metadata={"identifier_only": True}, now=now)
    return SourceRepository(project_path).upsert(record)


def _authors_from_bib(value: str) -> list[str]:
    return [item.strip() for item in re.split(r"\s+and\s+", value, flags=re.IGNORECASE) if item.strip()]


def import_bibtex(project_path: Path, path: Path, *, now: Callable[[], str] = utc_now) -> list[dict[str, Any]]:
    try:
        database = bibtexparser.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProjectError(f"cannot parse BibTeX {path}: {exc}") from exc
    if not database.entries:
        raise ProjectError("BibTeX contains no parseable entries with an explicit citation key")
    repository = SourceRepository(project_path)
    results = []
    for entry in database.entries:
        lower = {str(key).lower(): value for key, value in entry.items()}
        citation_id = str(entry.get("ID", "")).strip()
        if not citation_id and not any(lower.get(key) for key in ("doi", "url", "eprint")):
            raise ProjectError("identifierless BibTeX entry requires an explicit citation key")
        record = _base_record(source_type="bibtex", title=str(lower.get("title", "")), authors=_authors_from_bib(str(lower.get("author", ""))), published=str(lower.get("year")) if lower.get("year") else None, doi=str(lower.get("doi")) if lower.get("doi") else None, arxiv=str(lower.get("eprint")) if str(lower.get("archiveprefix", "")).lower() == "arxiv" else None, url=str(lower.get("url")) if lower.get("url") else None, content_hash=None, state="metadata_resolved", data_policy="public_only", import_method="bibtex", metadata={"bibtex_records": [dict(entry)]}, citation_key=f"bibtex:{citation_id}" if citation_id else None, now=now)
        results.append(repository.upsert(record)[0])
    _render_reference_files(project_path.resolve())
    return results


def _preferred_raw(records: list[dict[str, Any]]) -> dict[str, Any]:
    return max(records, key=lambda item: (len(item), canonical_json(item)))


def _render_reference_files(root: Path) -> None:
    sources = SourceRepository(root).all()
    bib_pairs = [
        (record["source_id"], _preferred_raw(record["metadata"]["bibtex_records"]))
        for record in sources
        if record["metadata"].get("bibtex_records")
    ]
    bib_id_counts: dict[str, int] = {}
    for _, entry in bib_pairs:
        bib_id_counts[str(entry.get("ID", ""))] = bib_id_counts.get(str(entry.get("ID", "")), 0) + 1
    bib_entries = []
    for source_id, raw_entry in bib_pairs:
        entry = dict(raw_entry)
        if bib_id_counts.get(str(entry.get("ID", "")), 0) > 1:
            entry["ID"] = f"{entry['ID']}__{source_id.removeprefix('src_')[:8]}"
        bib_entries.append(entry)
    database = BibDatabase()
    database.entries = sorted(bib_entries, key=lambda item: str(item.get("ID", "")))
    writer = BibTexWriter()
    writer.order_entries_by = ("ID",)
    atomic_write_text(
        confined_project_path(root, "references/references.bib"),
        bibtexparser.dumps(database, writer=writer) if bib_entries else "",
    )
    csl_pairs = [
        (record["source_id"], _preferred_raw(record["metadata"]["csl_records"]))
        for record in sources
        if record["metadata"].get("csl_records")
    ]
    csl_id_counts: dict[str, int] = {}
    for _, entry in csl_pairs:
        csl_id_counts[str(entry.get("id", ""))] = csl_id_counts.get(str(entry.get("id", "")), 0) + 1
    csl_entries = []
    for source_id, raw_entry in csl_pairs:
        entry = dict(raw_entry)
        if csl_id_counts.get(str(entry.get("id", "")), 0) > 1:
            entry["id"] = f"{entry['id']}__{source_id.removeprefix('src_')[:8]}"
        csl_entries.append(entry)
    atomic_write_json(
        confined_project_path(root, "references/references.csl.json"),
        sorted(csl_entries, key=canonical_json),
    )


def _csl_authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for author in value:
        if isinstance(author, dict):
            literal = author.get("literal") or " ".join(str(author.get(key, "")).strip() for key in ("given", "family")).strip()
            if literal:
                result.append(str(literal))
    return result


def import_csl_json(project_path: Path, path: Path, *, now: Callable[[], str] = utc_now) -> list[dict[str, Any]]:
    value = read_json(path)
    entries = value if isinstance(value, list) else [value]
    if not all(isinstance(item, dict) for item in entries):
        raise ProjectError("CSL-JSON must contain an object or array of objects")
    repository = SourceRepository(project_path)
    results = []
    for entry in entries:
        issued = entry.get("issued", {})
        date_parts = issued.get("date-parts", []) if isinstance(issued, dict) else []
        published = str(date_parts[0][0]) if date_parts and date_parts[0] else None
        citation_id = str(entry.get("id", "")).strip()
        if not citation_id and not any(entry.get(key) for key in ("DOI", "arxiv", "URL")):
            raise ProjectError("identifierless CSL-JSON entry requires an explicit item id")
        record = _base_record(source_type="csl_json", title=str(entry.get("title", "")), authors=_csl_authors(entry.get("author")), published=published, doi=str(entry.get("DOI")) if entry.get("DOI") else None, arxiv=str(entry.get("arxiv")) if entry.get("arxiv") else None, url=str(entry.get("URL")) if entry.get("URL") else None, content_hash=None, state="metadata_resolved", data_policy="public_only", import_method="csl-json", metadata={"csl_records": [dict(entry)]}, citation_key=f"csl:{citation_id}" if citation_id else None, now=now)
        results.append(repository.upsert(record)[0])
    _render_reference_files(project_path.resolve())
    return results


def import_local_document(
    project_path: Path, path: Path, *, kind: str, private: bool = False, retain_copy: bool = False,
    now: Callable[[], str] = utc_now,
) -> tuple[dict[str, Any], bool]:
    root = project_path.resolve()
    source_path = path.resolve()
    if not source_path.is_file():
        raise ProjectError(f"local source does not exist: {source_path}")
    content = source_path.read_bytes()
    if len(content) > 15 * 1024 * 1024:
        raise ProjectError("local source exceeds 15 MiB limit")
    if kind == "pdf":
        title, extraction = _extract_pdf(content)
        extraction_engine = _engine(
            "pypdf", "pypdf", {"max_pages": MAX_PDF_PAGES, "max_chars": MAX_EXTRACTED_CHARS}
        )
        title = title or source_path.stem
        suffix = ".pdf"
    elif kind in {"markdown", "outline"}:
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ProjectError(f"invalid UTF-8 in {source_path}: {exc}") from exc
        extraction = _extract_markdown(text)
        extraction_engine = _markdown_engine()
        heading = re.search(r"^#\s+(.+)$", text, flags=re.MULTILINE)
        title = heading.group(1).strip() if heading else source_path.stem
        suffix = ".md"
    else:
        raise ProjectError(f"unsupported local document kind: {kind}")
    content_hash = sha256_bytes(content)
    record = _base_record(source_type=kind, title=title, authors=[], published=None, doi=None, arxiv=None, url=None, content_hash=content_hash, state="content_inspected", data_policy=_policy(root, private), import_method=kind, metadata={"original_name": source_path.name}, retrieval={"retrieved_at": now(), "final_url": None, "http_status": None, "content_type": "application/pdf" if kind == "pdf" else "text/markdown", "charset": None if kind == "pdf" else "utf-8", "content_bytes": len(content)}, extraction_engine=extraction_engine, extraction=extraction, now=now)
    if retain_copy:
        record["local_copy_path"] = _retain_copy(root, record["source_id"], suffix, content)
        record["state"] = "local_copy_retained"
    return SourceRepository(root).upsert(record, extraction=extraction)


def load_extraction(
    project_path: Path,
    source_id: str,
    source_hash: str | None = None,
    source_version: str | None = None,
) -> dict[str, Any]:
    root = project_path.resolve()
    source = SourceRepository(root).get(source_id)
    effective_hash = source_hash or source.get("content_hash")
    if not effective_hash:
        raise ProjectError(f"source has no inspected content version: {source_id}")
    matching_versions = [
        item for item in source["versions"]
        if item["content_hash"] == effective_hash
        and (source_version is None or item["source_version"] == source_version)
    ]
    if source_version is None and source.get("content_hash") == effective_hash:
        matching_versions = [
            item for item in matching_versions if item["source_version"] == source["source_version"]
        ]
    if len(matching_versions) != 1:
        raise ProjectError("extraction lookup requires one exact source hash/version")
    version = matching_versions[0]
    identities = [source_id, source["source_id"], *source.get("aliases", [])]
    name = _version_filename(effective_hash, version["source_version"])
    for identity in dict.fromkeys(identities):
        path = confined_project_path(root, Path(".soleresearch/extractions") / identity / name)
        if path.is_file():
            extraction = read_json(path)
            if not isinstance(extraction, dict) or _extraction_hash(extraction) != version["extraction_hash"]:
                raise ProjectError(f"extraction cache integrity failure for {source_id} at {version['source_version']}")
            return extraction
    raise ProjectError(f"missing extraction for {source_id} at {effective_hash}/{version['source_version']}")
