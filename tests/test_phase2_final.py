from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

import soleresearch.sources as source_module
from soleresearch.errors import ProjectError
from soleresearch.evidence import EvidenceRepository, locate_excerpt, locator
from soleresearch.project import initialize_project
from soleresearch.retrieval import BoundedRetriever, RetrievalError
from soleresearch.sources import (
    SourceRepository,
    import_csl_json,
    import_identifier,
    import_local_document,
    import_url,
    load_extraction,
)

NOW_1 = lambda: "2026-07-10T12:00:00Z"
NOW_2 = lambda: "2026-07-10T13:00:00Z"
PUBLIC = lambda _host: ["93.184.216.34"]


def _retriever(handler, *, sleeper=lambda _seconds: None) -> BoundedRetriever:
    return BoundedRetriever(
        transport=httpx.MockTransport(handler), resolver=PUBLIC, sleeper=sleeper, clock=lambda: 0.0
    )


def _write_csl(path: Path, value: dict[str, object]) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def _write_caption_pdf(path: Path) -> None:
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
    stream.set_data(
        b"BT /F1 12 Tf 72 720 Td (Figure 3: PDF wear context.) Tj 0 -20 Td (Table 4: PDF miles context.) Tj ET"
    )
    page[NameObject("/Contents")] = stream
    with path.open("wb") as handle:
        writer.write(handle)


def test_same_raw_content_new_engine_preserves_derived_versions_and_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    note = tmp_path / "note.md"
    note.write_text("Immutable raw passage.", encoding="utf-8")
    first, _ = import_local_document(root, note, kind="markdown", now=NOW_1)
    old_version = first["source_version"]
    old_extraction = load_extraction(root, first["source_id"], first["content_hash"], old_version)
    evidence, _ = EvidenceRepository(root).add(
        source_id=first["source_id"], source_hash=first["content_hash"], source_version=old_version,
        locator=locate_excerpt(old_extraction, "Immutable raw passage."), excerpt="Immutable raw passage.",
        paraphrase="Original extraction.", stance="context", actor_type="human", actor_id="reader",
        method="read", now=NOW_1,
    )
    monkeypatch.setattr(
        source_module,
        "_markdown_engine",
        lambda: {"name": "soleresearch-markdown", "version": "2", "config": {"max_chars": 1234}},
    )
    second, created = import_local_document(root, note, kind="markdown", now=NOW_2)
    assert created is False and second["content_hash"] == first["content_hash"]
    assert second["source_version"] != old_version and len(second["versions"]) == 2
    assert load_extraction(root, evidence["source_id"], evidence["source_hash"], evidence["source_version"])["text"] == old_extraction["text"]
    EvidenceRepository(root).add(
        source_id=second["source_id"], source_hash=first["content_hash"], source_version=old_version,
        locator=evidence["locator"], excerpt=evidence["excerpt"], paraphrase="Old version still resolves.",
        stance="context", actor_type="agent", actor_id="verifier", method="verify", now=NOW_2,
    )


def test_extraction_cache_integrity_is_verified(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    note = tmp_path / "note.md"
    note.write_text("Integrity passage.", encoding="utf-8")
    source, _ = import_local_document(root, note, kind="markdown", now=NOW_1)
    cache = next((root / ".soleresearch/extractions").rglob("*.json"))
    cache.write_text('{"text":"tampered"}\n', encoding="utf-8")
    with pytest.raises(ProjectError, match="integrity failure"):
        load_extraction(root, source["source_id"], source["content_hash"], source["source_version"])


def test_merge_preserves_human_state_quality_richer_metadata_and_provenance_both_orders(
    tmp_path: Path,
) -> None:
    outcomes = []
    for reverse in (False, True):
        root = tmp_path / f"project-{reverse}"
        initialize_project(root)
        sparse = tmp_path / f"sparse-{reverse}.json"
        rich = tmp_path / f"rich-{reverse}.json"
        bridge = tmp_path / f"bridge-{reverse}.json"
        _write_csl(sparse, {"id": "sparse", "type": "article", "title": "Short", "DOI": "10.1234/merge", "URL": "https://a.example/item"})
        _write_csl(
            rich,
            {
                "id": "rich", "type": "article-journal", "title": "A substantially richer resolved title",
                "author": [{"given": "Ada", "family": "Runner"}], "URL": "https://b.example/item",
            },
        )
        paths = [rich, sparse] if reverse else [sparse, rich]
        records = [import_csl_json(root, path, now=NOW_2 if path == rich else NOW_1)[0] for path in paths]
        by_url = {record["canonical_url"]: record for record in records}
        a = by_url["https://a.example/item"]
        b = by_url["https://b.example/item"]
        SourceRepository(root).set_reading_state(a["source_id"], "queued")
        SourceRepository(root).set_reading_state(b["source_id"], "read")
        SourceRepository(root).set_quality(a["source_id"], authority="high", notes="Authority reviewed")
        SourceRepository(root).set_quality(b["source_id"], relevance="high", notes="Relevance reviewed")
        _write_csl(
            bridge,
            {"id": "sparse", "type": "article", "title": "Bridge", "DOI": "10.1234/merge", "URL": "https://b.example/item"},
        )
        merged = import_csl_json(root, bridge, now=lambda: "2026-07-10T14:00:00Z")[0]
        assert len(SourceRepository(root).all()) == 1
        assert merged["human_reading_state"] == "read"
        assert merged["quality"]["authority"] == merged["quality"]["relevance"] == "high"
        assert merged["quality"]["notes"] == "Authority reviewed\n\nRelevance reviewed"
        assert merged["title"] == "Bridge"
        assert merged["authors"] == ["Ada Runner"]
        assert len(merged["import_provenance"]) == 3
        outcomes.append(
            {key: merged[key] for key in ("source_id", "human_reading_state", "quality", "title", "authors")}
        )
    assert outcomes[0] == outcomes[1]


def test_conflicting_nondefault_quality_fails_explicitly(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    a_path, b_path, bridge_path = tmp_path / "a.json", tmp_path / "b.json", tmp_path / "bridge.json"
    _write_csl(a_path, {"id": "a", "type": "article", "DOI": "10.1234/conflict", "URL": "https://a.example"})
    _write_csl(b_path, {"id": "b", "type": "article", "URL": "https://b.example"})
    a = import_csl_json(root, a_path, now=NOW_1)[0]
    b = import_csl_json(root, b_path, now=NOW_1)[0]
    SourceRepository(root).set_quality(a["source_id"], authority="high")
    SourceRepository(root).set_quality(b["source_id"], authority="low")
    _write_csl(bridge_path, {"id": "a", "type": "article", "DOI": "10.1234/conflict", "URL": "https://b.example"})
    with pytest.raises(ProjectError, match="conflicting source quality dimension authority"):
        import_csl_json(root, bridge_path, now=NOW_2)
    assert len(SourceRepository(root).all()) == 2


def test_explicit_citation_ids_are_stable_and_missing_ids_reject(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    first_path, correction_path, missing_path = tmp_path / "first.json", tmp_path / "correction.json", tmp_path / "missing.json"
    _write_csl(first_path, {"id": "item-7", "type": "article", "title": "Draft"})
    _write_csl(
        correction_path,
        {"id": "item-7", "type": "article", "title": "Corrected and richer citation title", "author": [{"literal": "Runner Lab"}]},
    )
    first = import_csl_json(root, first_path, now=NOW_1)[0]
    corrected = import_csl_json(root, correction_path, now=NOW_2)[0]
    assert corrected["source_id"] == first["source_id"]
    assert corrected["title"] == "Corrected and richer citation title"
    assert corrected["citation_keys"] == ["csl:item-7"]
    _write_csl(missing_path, {"type": "article", "title": "No immutable identity"})
    with pytest.raises(ProjectError, match="explicit item id"):
        import_csl_json(root, missing_path, now=NOW_1)
    bib = tmp_path / "missing.bib"
    bib.write_text("@misc{, title={No key}}\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="citation key"):
        source_module.import_bibtex(root, bib, now=NOW_1)


def test_doi_ascii_folding_unicode_distinction_and_arxiv_era_edges(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    upper, _ = import_identifier(root, "10.1234/ABC-Ä", kind="doi", now=NOW_1)
    ascii_lower, created = import_identifier(root, "10.1234/abc-Ä", kind="doi", now=NOW_2)
    unicode_lower, unicode_created = import_identifier(root, "10.1234/abc-ä", kind="doi", now=NOW_2)
    assert created is False and upper["source_id"] == ascii_lower["source_id"]
    assert unicode_created is True and unicode_lower["source_id"] != upper["source_id"]
    for valid in ("0704.0001", "1412.9999v1", "1501.00001v2", "hep-th/9901001v3"):
        import_identifier(root, valid, kind="arxiv", now=NOW_1)
    for invalid in ("0703.0001", "1412.10000", "1501.0001", "1501.00001v0", "1513.00001"):
        with pytest.raises(ProjectError, match="invalid arXiv"):
            import_identifier(root, invalid, kind="arxiv", now=NOW_1)


def test_pdf_figure_and_table_artifacts_require_exact_context_offsets(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    pdf = tmp_path / "captions.pdf"
    _write_caption_pdf(pdf)
    source, _ = import_local_document(root, pdf, kind="pdf", now=NOW_1)
    extraction = load_extraction(root, source["source_id"])
    labels = {(item["type"], item["label"]) for item in extraction["artifacts"]}
    assert ("figure", "Figure 3") in labels and ("table", "Table 4") in labels
    figure_context = next(item["context"] for item in extraction["artifacts"] if item["type"] == "figure")
    excerpt = "PDF wear context."
    start = figure_context.index(excerpt)
    evidence, _ = EvidenceRepository(root).add(
        source_id=source["source_id"], locator=locator("figure", figure="Figure 3", page=1, start_char=start, end_char=start + len(excerpt)),
        excerpt=excerpt, paraphrase="PDF figure context.", stance="context", actor_type="human",
        actor_id="reader", method="pdf", now=NOW_1,
    )
    assert evidence["locator"]["page"] == 1


def test_timestamp_without_timeline_fails_but_markdown_segment_resolves(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    plain = tmp_path / "plain.md"
    plain.write_text("No timeline here.", encoding="utf-8")
    plain_source, _ = import_local_document(root, plain, kind="markdown", now=NOW_1)
    with pytest.raises(ProjectError, match="inspected timeline"):
        EvidenceRepository(root).add(
            source_id=plain_source["source_id"], locator=locator("timestamp", timestamp="00:01", section="Document", start_char=0, end_char=2),
            excerpt="No", paraphrase="No timeline.", stance="context", actor_type="human",
            actor_id="reader", method="read", now=NOW_1,
        )
    transcript = tmp_path / "transcript.md"
    transcript.write_text("# Interview\n\n[01:02:03] Runner reports tread slip.", encoding="utf-8")
    source, _ = import_local_document(root, transcript, kind="markdown", now=NOW_1)
    excerpt = "tread slip"
    context = "Runner reports tread slip."
    start = context.index(excerpt)
    record, _ = EvidenceRepository(root).add(
        source_id=source["source_id"], locator=locator("timestamp", timestamp="01:02:03", section="Interview", start_char=start, end_char=start + len(excerpt)),
        excerpt=excerpt, paraphrase="Runner reports slip.", stance="context", actor_type="human",
        actor_id="reader", method="transcript", now=NOW_1,
    )
    assert record["locator"]["timestamp"] == "01:02:03"


def test_redirect_and_retry_counters_are_independent_and_bounded() -> None:
    calls = 0

    def five_redirects(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls <= 5:
            return httpx.Response(302, headers={"location": f"https://example.org/r{calls}"})
        return httpx.Response(200, content=b"success")

    with _retriever(five_redirects) as retriever:
        assert retriever.retrieve("https://example.org/start").content == b"success"
    assert calls == 6

    redirects = 0

    def six_redirects(_request: httpx.Request) -> httpx.Response:
        nonlocal redirects
        redirects += 1
        return httpx.Response(302, headers={"location": f"https://example.org/x{redirects}"})

    with _retriever(six_redirects) as retriever:
        with pytest.raises(RetrievalError, match="5 redirects"):
            retriever.retrieve("https://example.org/start")
    assert redirects == 6

    failures = 0

    def three_failures(_request: httpx.Request) -> httpx.Response:
        nonlocal failures
        failures += 1
        return httpx.Response(503) if failures <= 2 else httpx.Response(200, content=b"recovered")

    with _retriever(three_failures) as retriever:
        assert retriever.retrieve("https://example.org/retry").content == b"recovered"
    assert failures == 3


def test_non_document_media_rejects_and_html_title_entities_decode(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    with _retriever(lambda _request: httpx.Response(200, headers={"content-type": "image/png"}, content=b"png")) as retriever:
        with pytest.raises(ProjectError, match="unsupported non-document media type"):
            import_url(root, "https://example.org/image", inspect=True, retriever=retriever, now=NOW_1)
    html_body = b"<html><head><title>Wear &amp; Miles</title></head><body><p>A sufficiently detailed research document body for extraction.</p></body></html>"
    with _retriever(lambda _request: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=html_body)) as retriever:
        source, _ = import_url(root, "https://example.org/paper", inspect=True, retriever=retriever, now=NOW_1)
    assert source["title"] == "Wear & Miles"


def test_same_local_citation_key_cannot_merge_conflicting_strong_identities(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    csl_a, csl_b = tmp_path / "csl-a.json", tmp_path / "csl-b.json"
    _write_csl(csl_a, {"id": "local-key", "type": "article", "DOI": "10.1234/one"})
    _write_csl(csl_b, {"id": "local-key", "type": "article", "DOI": "10.1234/two"})
    import_csl_json(root, csl_a, now=NOW_1)
    import_csl_json(root, csl_b, now=NOW_2)
    assert len(SourceRepository(root).all()) == 2
    csl_projection = json.loads((root / "references/references.csl.json").read_text(encoding="utf-8"))
    assert len(csl_projection) == 2 and len({item["id"] for item in csl_projection}) == 2

    bib_a, bib_b = tmp_path / "bib-a.bib", tmp_path / "bib-b.bib"
    bib_a.write_text("@article{same, doi={10.1234/bib-one}, title={One}}\n", encoding="utf-8")
    bib_b.write_text("@article{same, doi={10.1234/bib-two}, title={Two}}\n", encoding="utf-8")
    source_module.import_bibtex(root, bib_a, now=NOW_1)
    source_module.import_bibtex(root, bib_b, now=NOW_2)
    assert len(SourceRepository(root).all()) == 4
    projected = source_module.bibtexparser.loads(
        (root / "references/references.bib").read_text(encoding="utf-8")
    ).entries
    assert len(projected) == 2 and len({item["ID"] for item in projected}) == 2


def test_identifierless_citation_correction_merges_and_later_shorter_metadata_wins(
    tmp_path: Path,
) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    first_path, correction_path = tmp_path / "first.json", tmp_path / "correction.json"
    _write_csl(
        first_path,
        {"id": "correction-key", "type": "article", "title": "An initially very long metadata title", "author": [{"literal": "Old Author"}]},
    )
    _write_csl(
        correction_path,
        {"id": "correction-key", "type": "article", "title": "Correct", "author": [{"literal": "New"}]},
    )
    first = import_csl_json(root, first_path, now=NOW_1)[0]
    corrected = import_csl_json(root, correction_path, now=NOW_2)[0]
    assert corrected["source_id"] == first["source_id"]
    assert corrected["title"] == "Correct" and corrected["authors"] == ["New"]
    assert len(corrected["metadata"]["csl_records"]) == 2
    assert {item["import_method"] for item in corrected["import_provenance"]} == {"csl-json"}


def test_evidence_defaults_to_current_engine_version_and_cli_selects_historical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from soleresearch.cli import main

    root = tmp_path / "project"
    initialize_project(root)
    note = tmp_path / "note.md"
    note.write_text("Version selection passage.", encoding="utf-8")
    old, _ = import_local_document(root, note, kind="markdown", now=NOW_1)
    old_version = old["source_version"]
    monkeypatch.setattr(
        source_module,
        "_markdown_engine",
        lambda: {"name": "soleresearch-markdown", "version": "next", "config": {"mode": "new"}},
    )
    current, _ = import_local_document(root, note, kind="markdown", now=NOW_2)
    exact = locate_excerpt(load_extraction(root, current["source_id"]), "Version selection passage.")
    default_record, _ = EvidenceRepository(root).add(
        source_id=current["source_id"], locator=exact, excerpt="Version selection passage.",
        paraphrase="Current extraction.", stance="context", actor_type="human", actor_id="reader",
        method="read", now=NOW_2,
    )
    assert default_record["source_version"] == current["source_version"] != old_version

    assert main(
        [
            "evidence", str(root), "add", "--source-id", current["source_id"],
            "--source-hash", old["content_hash"], "--source-version", old_version,
            "--locator", "captured_passage", "--excerpt", "Version selection passage.",
            "--paraphrase", "Historical extraction.", "--stance", "context",
        ]
    ) == 0
    historical_id = json.loads(capsys.readouterr().out)["evidence_id"]
    historical = next(item for item in EvidenceRepository(root).all() if item["evidence_id"] == historical_id)
    assert historical["source_version"] == old_version


def test_three_retryable_attempts_allow_only_two_failures_before_success() -> None:
    calls = 0

    def succeeds_third(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503) if calls < 3 else httpx.Response(200, content=b"third")

    with _retriever(succeeds_third) as retriever:
        assert retriever.retrieve("https://example.org/retry").content == b"third"
    assert calls == 3

    failures = 0

    def always_fails(_request: httpx.Request) -> httpx.Response:
        nonlocal failures
        failures += 1
        return httpx.Response(503)

    with _retriever(always_fails) as retriever:
        with pytest.raises(RetrievalError, match="3 retryable attempts"):
            retriever.retrieve("https://example.org/fail")
    assert failures == 3


def test_read_and_skipped_merge_requires_human_resolution(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    a_path, b_path, bridge_path = tmp_path / "read.json", tmp_path / "skip.json", tmp_path / "bridge-read.json"
    _write_csl(a_path, {"id": "read", "type": "article", "DOI": "10.1234/read-skip", "URL": "https://a.example/read"})
    _write_csl(b_path, {"id": "skip", "type": "article", "URL": "https://b.example/skip"})
    read_source = import_csl_json(root, a_path, now=NOW_1)[0]
    skipped_source = import_csl_json(root, b_path, now=NOW_1)[0]
    SourceRepository(root).set_reading_state(read_source["source_id"], "read")
    SourceRepository(root).set_reading_state(skipped_source["source_id"], "skipped")
    _write_csl(
        bridge_path,
        {"id": "read", "type": "article", "DOI": "10.1234/read-skip", "URL": "https://b.example/skip"},
    )
    with pytest.raises(ProjectError, match="read/skipped"):
        import_csl_json(root, bridge_path, now=NOW_2)
    assert len(SourceRepository(root).all()) == 2
