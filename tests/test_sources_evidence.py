from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from soleresearch.cli import main
from soleresearch.errors import ProjectError
from soleresearch.evidence import EvidenceRepository, locate_excerpt, locator
from soleresearch.indexing import read_index_snapshot, rebuild_index
from soleresearch.project import initialize_project, load_project
from soleresearch.retrieval import BoundedRetriever, MAX_RESPONSE_BYTES, RetrievalError, validate_public_url
from soleresearch.sources import (
    SourceRepository,
    canonicalize_url,
    import_bibtex,
    import_csl_json,
    import_identifier,
    import_local_document,
    import_url,
    load_extraction,
    normalize_arxiv,
    normalize_doi,
    render_reading_queue,
)

NOW = lambda: "2026-07-10T12:00:00Z"
PUBLIC_RESOLVER = lambda _host: ["93.184.216.34"]


def _mock_retriever(handler) -> BoundedRetriever:
    return BoundedRetriever(
        transport=httpx.MockTransport(handler),
        resolver=PUBLIC_RESOLVER,
        sleeper=lambda _seconds: None,
    )


def _pdf(path: Path, text: str = "Page one evidence") -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = StreamObject()
    stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii"))
    page[NameObject("/Contents")] = stream
    with path.open("wb") as handle:
        writer.write(handle)


def test_identifier_and_url_normalization() -> None:
    assert normalize_doi("https://doi.org/10.1000/ABC.") == "10.1000/ABC."
    assert normalize_arxiv("arXiv:2401.01234v3") == "2401.01234v3"
    assert canonicalize_url("HTTPS://Example.COM:443/a//b/?utm_source=x&b=2&a=1#part") == "https://example.com/a//b/?b=2&a=1"
    assert canonicalize_url("https://user:pass@example.org/paper") is None


def test_deduplication_precedence_and_idempotent_imports(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    doi_record, created = import_identifier(root, "10.1000/ABC", kind="doi", now=NOW)
    assert created is True

    bib = tmp_path / "items.bib"
    bib.write_text(
        "@article{one, title={A Study}, author={A One and B Two}, year={2024}, doi={10.1000/abc}, url={https://elsewhere.example/paper}}\n",
        encoding="utf-8",
    )
    records = import_bibtex(root, bib, now=NOW)
    assert records[0]["source_id"] == doi_record["source_id"]
    assert len(SourceRepository(root).all()) == 1

    again, created = import_identifier(root, "https://doi.org/10.1000/abc", kind="doi", now=NOW)
    assert created is False
    assert again == SourceRepository(root).all()[0]


def test_csl_json_import_deduplicates_by_canonical_url(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    csl = tmp_path / "items.json"
    csl.write_text(
        json.dumps(
            [
                {
                    "id": "a",
                    "type": "article",
                    "title": "URL paper",
                    "URL": "https://example.org/paper?utm_source=x",
                    "author": [{"given": "Ada", "family": "Lovelace"}],
                    "issued": {"date-parts": [[2025]]},
                },
                {
                    "id": "b",
                    "type": "article",
                    "title": "Duplicate",
                    "URL": "https://example.org/paper",
                },
            ]
        ),
        encoding="utf-8",
    )
    imported = import_csl_json(root, csl, now=NOW)
    assert imported[0]["source_id"] == imported[1]["source_id"]
    assert len(SourceRepository(root).all()) == 1
    assert len(json.loads((root / "references/references.csl.json").read_text())) == 1


def test_discovery_metadata_cannot_support_evidence(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    source, _ = import_url(root, "https://example.org/paper", now=NOW)
    assert source["state"] == "discovered"

    with pytest.raises(ProjectError, match="discovery metadata"):
        EvidenceRepository(root).add(
            source_id=source["source_id"],
            locator=locator("paragraph", paragraph=1),
            excerpt="claim",
            paraphrase="A claim.",
            stance="supports",
            actor_type="human",
            actor_id="tester",
            method="manual",
            now=NOW,
        )


def test_markdown_exact_locators_and_evidence_provenance(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    note = tmp_path / "note.md"
    note.write_text("# Wear\n\nFirst paragraph.\n\n## Result\n\nOutsole loss increased with miles.\n", encoding="utf-8")
    source, created = import_local_document(root, note, kind="markdown", now=NOW)
    assert created is True
    assert source["state"] == "content_inspected"

    evidence, evidence_created = EvidenceRepository(root).add(
        source_id=source["source_id"],
        locator=locator("section", section="Result", label="Result section"),
        excerpt="Outsole loss increased with miles.",
        paraphrase="Wear grew as mileage accumulated.",
        stance="supports",
        actor_type="human",
        actor_id="austin",
        method="manual-reading",
        run_id="run_1",
        task_id="task_1",
        now=NOW,
    )
    assert evidence_created is True
    assert evidence["source_hash"] == source["content_hash"]
    assert evidence["locator"]["section"] == "Result"
    assert evidence["attestations"][0]["actor_id"] == "austin"
    assert EvidenceRepository(root).add(
        source_id=source["source_id"], locator=evidence["locator"], excerpt=evidence["excerpt"],
        paraphrase=evidence["attestations"][0]["paraphrase"], stance=evidence["attestations"][0]["stance"], actor_type="human",
        actor_id="someone-else", method="repeat", now=NOW,
    )[1] is False


def test_captured_passage_requires_unique_verbatim_offsets(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    note = tmp_path / "note.md"
    note.write_text("# Note\n\nUnique passage here.\n", encoding="utf-8")
    source, _ = import_local_document(root, note, kind="outline", now=NOW)
    extraction = load_extraction(root, source["source_id"])
    exact = locate_excerpt(extraction, "Unique passage here.")
    record, _ = EvidenceRepository(root).add(
        source_id=source["source_id"], locator=exact, excerpt="Unique passage here.",
        paraphrase="A unique passage exists.", stance="context", actor_type="agent",
        actor_id="reader-1", method="captured-passage", now=NOW,
    )
    assert record["locator"]["end_char"] > record["locator"]["start_char"]


def test_pdf_import_and_one_based_page_evidence(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root, data_policy="local_private")
    pdf = tmp_path / "paper.pdf"
    _pdf(pdf)
    source, _ = import_local_document(root, pdf, kind="pdf", private=True, retain_copy=True, now=NOW)
    assert source["state"] == "local_copy_retained"
    assert (root / source["local_copy_path"]).is_file()

    evidence, _ = EvidenceRepository(root).add(
        source_id=source["source_id"], locator=locator("page", page=1, label="p. 1"),
        excerpt="Page one evidence", paraphrase="Evidence appears on page one.", stance="context",
        actor_type="human", actor_id="reader", method="pdf-reading", now=NOW,
    )
    assert evidence["locator"]["page"] == 1
    with pytest.raises(ProjectError, match="out of range"):
        EvidenceRepository(root).add(
            source_id=source["source_id"], locator=locator("page", page=2),
            excerpt="Page one evidence", paraphrase="Wrong page.", stance="context",
            actor_type="human", actor_id="reader", method="pdf-reading", now=NOW,
        )


def test_private_import_requires_private_project_policy(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    note = tmp_path / "private.md"
    note.write_text("private", encoding="utf-8")
    with pytest.raises(ProjectError, match="data_policy=local_private"):
        import_local_document(root, note, kind="markdown", private=True, now=NOW)


def test_bounded_url_retrieval_redirect_html_and_dedupe(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(str(request.url))
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "https://docs.example/final"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"<html><head><title>Study</title></head><body><h1>Result</h1><p>Observed wear pattern.</p></body></html>",
        )

    discovered, discovered_created = import_url(root, "https://docs.example/start", now=NOW)
    assert discovered_created is True
    with _mock_retriever(handler) as retriever:
        source, created = import_url(root, "https://docs.example/start", inspect=True, retriever=retriever, now=NOW)
    assert created is False
    assert discovered["source_id"] in source["aliases"]
    assert len(SourceRepository(root).all()) == 1
    assert source["state"] == "content_inspected"
    assert source["canonical_url"] == "https://docs.example/final"
    assert requests == ["https://docs.example/start", "https://docs.example/final"]

    extraction = load_extraction(root, source["source_id"])
    evidence, _ = EvidenceRepository(root).add(
        source_id=source["source_id"], locator=locate_excerpt(extraction, "Observed wear pattern."),
        excerpt="Observed wear pattern.", paraphrase="The study observed a pattern.", stance="supports",
        actor_type="agent", actor_id="reader", method="html-inspection", now=NOW,
    )
    assert evidence["source_version"].startswith("sv_")


def test_ssrf_and_size_boundaries_fail_closed() -> None:
    with pytest.raises(RetrievalError, match="private or reserved"):
        validate_public_url("http://localhost/test", resolver=lambda _host: ["127.0.0.1"])
    with pytest.raises(RetrievalError, match="credentials"):
        validate_public_url("https://user:pass@example.org/test", resolver=PUBLIC_RESOLVER)

    def oversized(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": str(MAX_RESPONSE_BYTES + 1)}, content=b"")

    with _mock_retriever(oversized) as retriever:
        with pytest.raises(RetrievalError, match="byte limit"):
            retriever.retrieve("https://example.org/large")


def test_reading_queue_quality_and_index_export_visibility(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    first, _ = import_identifier(root, "10.1000/one", kind="doi", now=NOW)
    second, _ = import_identifier(root, "2401.00001", kind="arxiv", now=NOW)
    repository = SourceRepository(root)
    repository.set_reading_state(second["source_id"], "queued")
    repository.set_quality(
        second["source_id"], authority="high", methodology_transparency="medium",
        evidence_directness="high", relevance="high", publication_status="peer_reviewed",
        notes="Primary study",
    )
    ordered = render_reading_queue(root)
    assert ordered[0]["source_id"] == second["source_id"]
    queue = (root / "sources/reading-queue.md").read_text(encoding="utf-8")
    assert "authority=high" in queue and "Primary study" in queue

    rebuilt = rebuild_index(root)
    assert rebuilt["ledgers"]["sources/sources.jsonl"] == 2
    assert {row[0] for row in read_index_snapshot(root)} >= {"sources/sources.jsonl"}


def test_cli_import_source_evidence_workflow(tmp_path: Path, capsys) -> None:
    root = tmp_path / "project"
    note = tmp_path / "note.md"
    note.write_text("# Note\n\nCLI evidence text.\n", encoding="utf-8")
    assert main(["init", str(root)]) == 0
    capsys.readouterr()
    assert main(["import", str(root), "markdown", str(note)]) == 0
    imported = json.loads(capsys.readouterr().out)
    source_id = imported["source_ids"][0]
    assert main(["source", str(root), "read", source_id, "--state", "queued"]) == 0
    capsys.readouterr()
    assert main(
        [
            "evidence", str(root), "add", "--source-id", source_id,
            "--locator", "captured_passage", "--excerpt", "CLI evidence text.",
            "--paraphrase", "The CLI captured evidence.", "--stance", "context",
        ]
    ) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["created"] is True
    assert main(["doctor", str(root)]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_malformed_source_and_evidence_contracts_fail_project_load(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    (root / "sources/sources.jsonl").write_text(
        '{"schema_version":1,"source_id":"incomplete"}\n', encoding="utf-8"
    )
    with pytest.raises(ProjectError, match="invalid sources/sources.jsonl:1"):
        load_project(root)
