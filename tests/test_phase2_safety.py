from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

import soleresearch.sources as source_module
from soleresearch.errors import ProjectError
from soleresearch.evidence import EvidenceRepository, locate_excerpt, locator
from soleresearch.project import initialize_project, load_project
from soleresearch.retrieval import BoundedRetriever, RetrievalError
from soleresearch.sources import (
    SourceRepository,
    canonicalize_url,
    import_csl_json,
    import_identifier,
    import_local_document,
    import_url,
    load_extraction,
)
from soleresearch.storage import canonical_json

NOW_1 = lambda: "2026-07-10T12:00:00Z"
NOW_2 = lambda: "2026-07-10T13:00:00Z"
PUBLIC = lambda _host: ["93.184.216.34"]


def _retriever(handler, **kwargs) -> BoundedRetriever:
    return BoundedRetriever(
        transport=httpx.MockTransport(handler),
        resolver=kwargs.pop("resolver", PUBLIC),
        sleeper=kwargs.pop("sleeper", lambda _seconds: None),
        clock=kwargs.pop("clock", lambda: 0.0),
        **kwargs,
    )


@pytest.mark.parametrize("target", ["source-copies", ".soleresearch/extractions", "sources"])
def test_project_writes_reject_symlink_components(target: str, tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    outside = tmp_path / "outside"
    outside.mkdir()
    note = tmp_path / "note.md"
    note.write_text("# Note\n\nconfined", encoding="utf-8")
    if target == "sources":
        real = root / "sources"
        for child in real.iterdir():
            (outside / child.name).write_bytes(child.read_bytes())
        for child in real.iterdir():
            child.unlink()
        real.rmdir()
        real.symlink_to(outside, target_is_directory=True)
        action = lambda: SourceRepository(root)
    else:
        link = root / target
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(outside, target_is_directory=True)
        action = lambda: import_local_document(
            root, note, kind="markdown", retain_copy=target == "source-copies", now=NOW_1
        )
    with pytest.raises(ProjectError, match="symbolic link"):
        action()
    assert not any(outside.glob("src_*"))


def test_bridge_import_merges_transitive_equivalence_and_preserves_evidence(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    first_csl = tmp_path / "first.json"
    first_csl.write_text(
        json.dumps({"id": "one", "type": "article", "title": "One", "DOI": "10.1234/CaseX", "URL": "https://a.example/p"}),
        encoding="utf-8",
    )
    doi_source = import_csl_json(root, first_csl, now=NOW_1)[0]

    body = b"<html><body><p>Bridge evidence.</p></body></html>"
    with _retriever(lambda _request: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=body)) as retriever:
        content_source, _ = import_url(root, "https://b.example/p", inspect=True, retriever=retriever, now=NOW_1)
    extraction = load_extraction(root, content_source["source_id"], content_source["content_hash"])
    evidence, _ = EvidenceRepository(root).add(
        source_id=content_source["source_id"], locator=locate_excerpt(extraction, "Bridge evidence."),
        excerpt="Bridge evidence.", paraphrase="Bridge evidence exists.", stance="context",
        actor_type="human", actor_id="reader", method="read", now=NOW_1,
    )

    bridge = tmp_path / "bridge.json"
    bridge.write_text(
        json.dumps({"id": "bridge", "type": "article", "title": "Bridge", "DOI": "10.1234/casex", "URL": "https://b.example/p"}),
        encoding="utf-8",
    )
    merged = import_csl_json(root, bridge, now=NOW_2)[0]
    records = SourceRepository(root).all()
    assert len(records) == 1
    assert doi_source["source_id"] in {merged["source_id"], *merged["aliases"]}
    assert content_source["source_id"] in merged["aliases"]
    assert SourceRepository(root).get(evidence["source_id"])["source_id"] == merged["source_id"]
    assert load_extraction(root, evidence["source_id"], evidence["source_hash"])["text"]
    load_project(root)


def test_strictest_data_policy_wins_all_merge_orders(tmp_path: Path) -> None:
    note = tmp_path / "same.md"
    note.write_text("same content", encoding="utf-8")
    for order in ((False, True), (True, False)):
        root = tmp_path / ("project-" + str(order[0]))
        initialize_project(root, data_policy="local_private")
        for private in order:
            import_local_document(root, note, kind="markdown", private=private, now=NOW_1)
        assert SourceRepository(root).all()[0]["data_policy"] == "local_private"


def test_immutable_extraction_versions_survive_refresh_and_old_evidence(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    bodies = [
        b"<html><body><h1>Study</h1><p>Old exact passage with enough study context for extraction.</p></body></html>",
        b"<html><body><h1>Study</h1><p>New exact passage with enough study context for extraction.</p></body></html>",
    ]
    with _retriever(lambda _request: httpx.Response(200, headers={"content-type": "text/html; charset=iso-8859-1"}, content=bodies[0])) as retriever:
        old, _ = import_url(root, "https://version.example/p", inspect=True, retriever=retriever, now=NOW_1)
    old_extraction = load_extraction(root, old["source_id"], old["content_hash"])
    old_evidence, _ = EvidenceRepository(root).add(
        source_id=old["source_id"], source_hash=old["content_hash"],
        locator=locate_excerpt(old_extraction, "Old exact passage with enough study context for extraction."), excerpt="Old exact passage with enough study context for extraction.",
        paraphrase="Old version statement.", stance="context", actor_type="human",
        actor_id="reader", method="read", now=NOW_1,
    )
    with _retriever(lambda _request: httpx.Response(200, headers={"content-type": "text/html; charset=utf-8"}, content=bodies[1])) as retriever:
        new, _ = import_url(root, "https://version.example/p", inspect=True, retriever=retriever, now=NOW_2)
    assert len(new["versions"]) == 2
    assert load_extraction(root, old_evidence["source_id"], old_evidence["source_hash"])["text"] == old_extraction["text"]
    assert {item["http_charset"] for item in new["versions"]} == {"iso-8859-1", "utf-8"}
    assert all(item["extraction_engine"]["name"] == "trafilatura" for item in new["versions"])
    load_project(root)


def test_extraction_is_staged_before_ledger_and_rolled_back_on_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    note = tmp_path / "note.md"
    note.write_text("staged extraction", encoding="utf-8")
    observed: list[Path] = []

    def fail_write(_path: Path, _records) -> None:
        observed.extend((root / ".soleresearch/extractions").rglob("*.json"))
        raise OSError("ledger failure")

    monkeypatch.setattr(source_module, "write_jsonl", fail_write)
    with pytest.raises(OSError, match="ledger failure"):
        import_local_document(root, note, kind="markdown", now=NOW_1)
    assert observed and not list((root / ".soleresearch/extractions").rglob("*.json"))
    assert (root / "sources/sources.jsonl").read_bytes() == b""


def test_evidence_identity_is_passage_only_and_attestations_are_preserved(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    note = tmp_path / "note.md"
    note.write_text("One exact claim.", encoding="utf-8")
    source, _ = import_local_document(root, note, kind="markdown", now=NOW_1)
    exact = locate_excerpt(load_extraction(root, source["source_id"]), "One exact claim.")
    first, created = EvidenceRepository(root).add(
        source_id=source["source_id"], locator=exact, excerpt="One exact claim.",
        paraphrase="Human paraphrase.", stance="supports", actor_type="human",
        actor_id="reader", method="read", run_id="run_h", task_id="task_h", now=NOW_1,
    )
    second, created_again = EvidenceRepository(root).add(
        source_id=source["source_id"], locator=exact, excerpt="One exact claim.",
        paraphrase="Agent paraphrase.", stance="qualifies", actor_type="agent",
        actor_id="reader-agent", method="read", run_id="run_a", task_id="task_a", now=NOW_2,
    )
    assert created is True and created_again is False
    assert first["evidence_id"] == second["evidence_id"]
    assert len(second["attestations"]) == 2
    assert {item["actor_type"] for item in second["attestations"]} == {"human", "agent"}
    same, _ = EvidenceRepository(root).add(
        source_id=source["source_id"], locator=exact, excerpt="One exact claim.",
        paraphrase="Agent paraphrase.", stance="qualifies", actor_type="agent",
        actor_id="reader-agent", method="read", run_id="run_a", task_id="task_a", now=NOW_2,
    )
    assert len(same["attestations"]) == 2


def test_identifier_raw_forms_versions_and_discovery_state(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    upper, _ = import_identifier(root, "10.1234/Opaque.Suffix)", kind="doi", now=NOW_1)
    lower, created = import_identifier(root, "10.1234/opaque.suffix)", kind="doi", now=NOW_2)
    assert created is False
    assert upper["identifiers"]["doi"] == "10.1234/Opaque.Suffix)"
    assert lower["identifiers"]["doi_key"] == "10.1234/opaque.suffix)"
    assert lower["state"] == "discovered"
    v2, _ = import_identifier(root, "2401.01234v2", kind="arxiv", now=NOW_1)
    v3, created = import_identifier(root, "2401.01234v3", kind="arxiv", now=NOW_2)
    assert created is False and v2["source_id"] == v3["source_id"]
    assert v3["identifiers"]["arxiv"] == "2401.01234v3"
    assert v3["identifiers"]["arxiv_base"] == "2401.01234"
    with pytest.raises(ProjectError, match="invalid arXiv"):
        import_identifier(root, "2413.00001", kind="arxiv", now=NOW_1)


def test_conservative_url_canonicalization_preserves_semantics() -> None:
    value = "HTTPS://Example.org:443/a//b/?z=2&z=1&utm_source=x&blank=#frag"
    assert canonicalize_url(value) == "https://example.org/a//b/?z=2&z=1&blank="
    assert canonicalize_url("https://example.org/a") != canonicalize_url("https://example.org/a/")


def test_resolved_address_is_pinned_and_dns_is_called_once() -> None:
    calls: list[str] = []
    observed: list[str] = []

    def resolver(host: str):
        calls.append(host)
        return ["93.184.216.34"] if len(calls) == 1 else ["127.0.0.1"]

    def handler(request: httpx.Request) -> httpx.Response:
        observed.append(request.extensions["soleresearch_resolved_ip"])
        return httpx.Response(200, content=b"ok")

    with _retriever(handler, resolver=resolver) as retriever:
        assert retriever.retrieve("https://example.org/p").content == b"ok"
    assert calls == ["example.org"]
    assert observed == ["93.184.216.34"]


class _BreakingStream(httpx.SyncByteStream):
    def __init__(self, closed: list[bool]) -> None:
        self.closed = closed

    def __iter__(self):
        yield b"partial"
        raise httpx.ReadError("midstream")

    def close(self) -> None:
        self.closed.append(True)


def test_midstream_transport_error_retries_closes_and_recovers() -> None:
    calls = 0
    closed: list[bool] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, stream=_BreakingStream(closed))
        return httpx.Response(200, content=b"complete")

    with _retriever(handler) as retriever:
        assert retriever.retrieve("https://example.org/p").content == b"complete"
    assert calls == 2 and closed


def test_total_attempt_bound_retry_after_and_pacing() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(302, headers={"location": f"https://example.org/{calls}"})

    with _retriever(handler, sleeper=sleeps.append) as retriever:
        with pytest.raises(RetrievalError, match="5 redirects"):
            retriever.retrieve("https://example.org/start")
    assert calls == 6 and any(delay == 1.0 for delay in sleeps)

    with _retriever(lambda _request: httpx.Response(429, headers={"retry-after": "31"})) as retriever:
        with pytest.raises(RetrievalError, match="Retry-After exceeds"):
            retriever.retrieve("https://example.org/rate")


def test_pdf_and_decoded_expansion_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    class Pages:
        def __len__(self) -> int:
            return source_module.MAX_PDF_PAGES + 1

    class Reader:
        pages = Pages()
        metadata = {}

    monkeypatch.setattr(source_module, "PdfReader", lambda _buffer: Reader())
    with pytest.raises(ProjectError, match="page limit"):
        source_module._extract_pdf(b"pdf")
    with pytest.raises(ProjectError, match="character limit"):
        source_module._extract_markdown("x" * (source_module.MAX_EXTRACTED_CHARS + 1))


def test_typed_locators_and_exact_passage_slice(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    note = tmp_path / "note.md"
    note.write_text("Figure 1 shows wear.\n\nTable 1 lists miles.\n\n[00:10] Tread loss visible.", encoding="utf-8")
    source, _ = import_local_document(root, note, kind="markdown", now=NOW_1)
    repository = EvidenceRepository(root)
    figure, _ = repository.add(
        source_id=source["source_id"], locator=locator("figure", figure="Figure 1", section="Document", start_char=9, end_char=20),
        excerpt="shows wear.", paraphrase="Figure shows wear.", stance="context",
        actor_type="human", actor_id="reader", method="read", now=NOW_1,
    )
    table, _ = repository.add(
        source_id=source["source_id"], locator=locator("table", table="Table 1", section="Document", start_char=8, end_char=20),
        excerpt="lists miles.", paraphrase="Table lists miles.", stance="context",
        actor_type="human", actor_id="reader", method="read", now=NOW_1,
    )
    assert figure["locator"]["figure"] == "Figure 1"
    assert table["locator"]["table"] == "Table 1"
    timestamp, _ = repository.add(
        source_id=source["source_id"], locator=locator("timestamp", timestamp="00:10", section="Document", start_char=0, end_char=19),
        excerpt="Tread loss visible.", paraphrase="Timestamped wear.", stance="context", actor_type="human",
        actor_id="reader", method="read", now=NOW_1,
    )
    assert timestamp["locator"]["timestamp"] == "00:10"
    with pytest.raises(ProjectError, match="offsets must slice"):
        repository.add(
            source_id=source["source_id"], locator=locator("captured_passage", start_char=0, end_char=9),
            excerpt="Figure", paraphrase="Bad offsets.", stance="context", actor_type="human",
            actor_id="reader", method="read", now=NOW_1,
        )


def test_project_load_rejects_duplicate_ids_and_invalid_state(tmp_path: Path) -> None:
    root = tmp_path / "project"
    initialize_project(root)
    source, _ = import_identifier(root, "10.1234/example", kind="doi", now=NOW_1)
    ledger = root / "sources/sources.jsonl"
    ledger.write_text(canonical_json(source) + "\n" + canonical_json(source) + "\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="duplicate source"):
        load_project(root)

    ledger.write_text(canonical_json({**source, "state": "content_inspected"}) + "\n", encoding="utf-8")
    with pytest.raises(ProjectError, match="lacks current immutable version"):
        load_project(root)
