from __future__ import annotations

import ipaddress
import http.client
import re
import socket
import ssl
import time
from dataclasses import dataclass
from typing import Callable, Iterable
from urllib.parse import urljoin, urlsplit

import httpx

from soleresearch.errors import SoleResearchError

MAX_RESPONSE_BYTES = 15 * 1024 * 1024
MAX_REDIRECTS = 5
MAX_RETRY_ATTEMPTS = 3
MAX_TOTAL_REQUESTS = 8
MAX_RETRY_AFTER_SECONDS = 30.0


class RetrievalError(SoleResearchError):
    """Raised when bounded retrieval cannot safely inspect a document."""


@dataclass(frozen=True)
class RetrievedDocument:
    requested_url: str
    final_url: str
    status_code: int
    content_type: str
    charset: str | None
    content: bytes


Resolver = Callable[[str], Iterable[str]]


def _system_resolver(host: str) -> list[str]:
    try:
        return sorted({item[4][0] for item in socket.getaddrinfo(host, None)})
    except socket.gaierror as exc:
        raise RetrievalError(f"cannot resolve host {host!r}: {exc}") from exc


def _is_forbidden_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    return not address.is_global


def validate_public_url(
    url: str,
    *,
    resolver: Resolver = _system_resolver,
    allow_private_for_tests: bool = False,
) -> str:
    return _resolve_public(url, resolver=resolver, allow_private_for_tests=allow_private_for_tests)[0]


def _resolve_public(
    url: str,
    *,
    resolver: Resolver,
    allow_private_for_tests: bool,
) -> tuple[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise RetrievalError("retrieval supports only http and https URLs")
    if parsed.username is not None or parsed.password is not None:
        raise RetrievalError("retrieval URLs must not contain credentials")
    host = parsed.hostname
    if not host:
        raise RetrievalError("retrieval URL must include a host")
    try:
        addresses = list(resolver(host))
    except RetrievalError:
        raise
    except Exception as exc:
        raise RetrievalError(f"cannot resolve host {host!r}: {exc}") from exc
    if not addresses:
        raise RetrievalError(f"host {host!r} resolved to no addresses")
    if not allow_private_for_tests:
        for address in addresses:
            try:
                forbidden = _is_forbidden_address(address)
            except ValueError as exc:
                raise RetrievalError(f"resolver returned invalid address for {host!r}: {address!r}") from exc
            if forbidden:
                raise RetrievalError(f"refusing private or reserved address for {host!r}: {address}")
    return host.lower().rstrip("."), sorted(addresses)[0]


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, domain: str, address: str, port: int, timeout: float) -> None:
        super().__init__(domain, port=port, timeout=timeout, context=ssl.create_default_context())
        self._address = address

    def connect(self) -> None:
        sock = socket.create_connection((self._address, self.port), self.timeout)
        sock.settimeout(30.0)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _HTTPResponseStream(httpx.SyncByteStream):
    def __init__(self, response: http.client.HTTPResponse, connection: http.client.HTTPConnection) -> None:
        self._response = response
        self._connection = connection

    def __iter__(self):
        try:
            while chunk := self._response.read(64 * 1024):
                yield chunk
        except (OSError, http.client.HTTPException) as exc:
            raise httpx.ReadError(str(exc)) from exc

    def close(self) -> None:
        self._response.close()
        self._connection.close()


class PinnedHTTPTransport(httpx.BaseTransport):
    """Small HTTP/1.1 transport that connects only to a pre-vetted IP address."""

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        address = request.extensions.get("soleresearch_resolved_ip")
        domain = request.extensions.get("soleresearch_domain")
        if not isinstance(address, str) or not isinstance(domain, str):
            raise httpx.ConnectError("missing vetted destination", request=request)
        port = request.url.port or (443 if request.url.scheme == "https" else 80)
        if request.url.scheme == "https":
            connection: http.client.HTTPConnection = _PinnedHTTPSConnection(domain, address, port, 10.0)
        else:
            connection = http.client.HTTPConnection(address, port=port, timeout=10.0)
        try:
            connection.putrequest(request.method, request.url.raw_path.decode("ascii"), skip_host=True, skip_accept_encoding=True)
            for key, value in request.headers.raw:
                connection.putheader(key.decode("ascii"), value.decode("latin-1"))
            connection.endheaders()
            response = connection.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            connection.close()
            raise httpx.ConnectError(str(exc), request=request) from exc
        return httpx.Response(
            response.status,
            headers=response.getheaders(),
            stream=_HTTPResponseStream(response, connection),
            request=request,
        )


class BoundedRetriever:
    """HTTP retriever with explicit SSRF, redirect, retry, pacing, and size bounds."""

    def __init__(
        self,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver = _system_resolver,
        sleeper: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        allow_private_for_tests: bool = False,
    ) -> None:
        self._client = httpx.Client(
            transport=transport or PinnedHTTPTransport(),
            follow_redirects=False,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=30.0, pool=10.0),
            headers={"User-Agent": "SoleResearch/0.1 (+local evidence workbench)"},
            trust_env=False,
        )
        self._resolver = resolver
        self._sleeper = sleeper
        self._clock = clock
        self._allow_private_for_tests = allow_private_for_tests
        self._last_request: dict[str, float] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> BoundedRetriever:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _pace(self, domain: str) -> None:
        previous = self._last_request.get(domain)
        now = self._clock()
        if previous is not None:
            remaining = 1.0 - (now - previous)
            if remaining > 0:
                self._sleeper(remaining)
        self._last_request[domain] = self._clock()

    def _request_once(self, url: str) -> httpx.Response:
        domain, address = _resolve_public(
            url,
            resolver=self._resolver,
            allow_private_for_tests=self._allow_private_for_tests,
        )
        self._pace(domain)
        request = self._client.build_request("GET", url)
        request.extensions["soleresearch_resolved_ip"] = address
        request.extensions["soleresearch_domain"] = domain
        return self._client.send(request, stream=True)

    def _retry_delay(self, response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("retry-after")
        if retry_after is None:
            return float(2 ** (attempt - 1))
        try:
            delay = float(retry_after)
        except ValueError as exc:
            raise RetrievalError("Retry-After must be delta seconds") from exc
        if delay < 0 or delay > MAX_RETRY_AFTER_SECONDS:
            raise RetrievalError(f"Retry-After exceeds {MAX_RETRY_AFTER_SECONDS:g} second bound")
        return delay

    def retrieve(self, url: str) -> RetrievedDocument:
        requested = url
        current = url
        redirects = 0
        retry_attempts = 0
        total_requests = 0
        while total_requests < MAX_TOTAL_REQUESTS:
            total_requests += 1
            response: httpx.Response | None = None
            try:
                response = self._request_once(current)
                if response.status_code in {429, 500, 502, 503, 504}:
                    retry_attempts += 1
                    if retry_attempts >= MAX_RETRY_ATTEMPTS:
                        raise RetrievalError(f"retrieval exhausted {MAX_RETRY_ATTEMPTS} retryable attempts at HTTP {response.status_code}")
                    self._sleeper(self._retry_delay(response, retry_attempts))
                    continue
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise RetrievalError("redirect response has no Location header")
                    redirects += 1
                    if redirects > MAX_REDIRECTS:
                        raise RetrievalError(f"retrieval exceeded {MAX_REDIRECTS} redirects")
                    current = urljoin(current, location)
                    continue
                retry_attempts += 1
                if retry_attempts > MAX_RETRY_ATTEMPTS:
                    raise RetrievalError(f"retrieval exceeded {MAX_RETRY_ATTEMPTS} retryable attempts")
                if response.status_code < 200 or response.status_code >= 300:
                    raise RetrievalError(f"retrieval returned HTTP {response.status_code} for {current}")
                declared = response.headers.get("content-length")
                if declared is not None:
                    try:
                        if int(declared) > MAX_RESPONSE_BYTES:
                            raise RetrievalError(f"response exceeds {MAX_RESPONSE_BYTES} byte limit")
                    except ValueError as exc:
                        raise RetrievalError("response has invalid Content-Length") from exc
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > MAX_RESPONSE_BYTES:
                        raise RetrievalError(f"response exceeds {MAX_RESPONSE_BYTES} byte limit")
                    chunks.append(chunk)
                content_type_header = response.headers.get("content-type", "application/octet-stream")
                content_type = content_type_header.split(";", 1)[0].strip().lower()
                charset_match = re.search(r"charset\s*=\s*[\"']?([^;\"']+)", content_type_header, flags=re.IGNORECASE)
                charset = charset_match.group(1).strip().lower() if charset_match else None
                return RetrievedDocument(requested, str(response.url), response.status_code, content_type, charset, b"".join(chunks))
            except httpx.TransportError as exc:
                retry_attempts += 1
                if retry_attempts >= MAX_RETRY_ATTEMPTS:
                    raise RetrievalError(f"retrieval exhausted {MAX_RETRY_ATTEMPTS} retryable attempts: {exc}") from exc
                self._sleeper(float(2 ** (retry_attempts - 1)))
            finally:
                if response is not None:
                    response.close()
        raise RetrievalError(f"retrieval exceeded {MAX_TOTAL_REQUESTS} total request bound")
