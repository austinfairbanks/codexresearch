"""Manual bounded local-HTTP source ingest smoke; not collected by pytest."""

from __future__ import annotations

import json
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, StreamObject

from soleresearch.project import initialize_project
from soleresearch.retrieval import BoundedRetriever
from soleresearch.sources import SourceRepository, import_url


def _pdf_bytes() -> bytes:
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
    content = StreamObject()
    content.set_data(b"BT /F1 12 Tf 72 720 Td (Fixture PDF evidence) Tj ET")
    page[NameObject("/Contents")] = content
    buffer = BytesIO()
    writer.write(buffer)
    return buffer.getvalue()


HTML = b"<html><head><title>Fixture HTML</title></head><body><p>Fixture HTML evidence.</p></body></html>"
PDF = _pdf_bytes()


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib handler contract
        if self.path == "/paper.pdf":
            content, content_type = PDF, "application/pdf"
        else:
            content, content_type = HTML, "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, _format: str, *_args: object) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "project"
            initialize_project(root)
            with BoundedRetriever(allow_private_for_tests=True) as retriever:
                html, _ = import_url(root, f"http://127.0.0.1:{server.server_port}/paper.html", inspect=True, retriever=retriever)
                pdf, _ = import_url(root, f"http://127.0.0.1:{server.server_port}/paper.pdf", inspect=True, retriever=retriever)
                again, created = import_url(root, f"http://127.0.0.1:{server.server_port}/paper.html", inspect=True, retriever=retriever)
            records = SourceRepository(root).all()
            assert created is False and again["source_id"] == html["source_id"]
            assert len(records) == 2 and pdf["retrieval"]["content_type"] == "application/pdf"
            print(json.dumps({"sources": len(records), "html": html["source_id"], "pdf": pdf["source_id"], "idempotent": True}, sort_keys=True))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
