"""Public-internet-only HTTPS transport with pinned DNS and bounded responses.

This module is deliberately separate from collection connectors. It is not
registered by the fixture orchestrator and does not enable live publisher
collection. A future adapter must be explicitly reviewed and wired in.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import socket
import ssl
import time
from typing import Callable, Mapping
from urllib.parse import urljoin, urlsplit


class TransportError(RuntimeError):
    """A request was rejected or could not be completed safely."""


class ResponseTooLarge(TransportError):
    """The response body exceeded the configured byte ceiling."""


@dataclass(frozen=True, slots=True)
class TransportResponse:
    requested_url: str
    final_url: str
    status: int
    headers: Mapping[str, str]
    body: bytes
    attempts: int
    redirects: int


def _canonical_host(host: str) -> str:
    if not isinstance(host, str) or not host:
        raise TransportError("URL host is missing")
    try:
        canonical = host.rstrip(".").encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise TransportError("URL host is invalid") from exc
    if not canonical or len(canonical) > 253:
        raise TransportError("URL host is invalid")
    try:
        ipaddress.ip_address(canonical)
    except ValueError:
        return canonical
    raise TransportError("IP-literal hosts are not allowed")


def _check_scope(host: str, allowed_domains: tuple[str, ...]) -> str:
    canonical = _canonical_host(host)
    domains = tuple(_canonical_host(item) for item in allowed_domains)
    if not domains or not any(
        canonical == domain or canonical.endswith("." + domain)
        for domain in domains
    ):
        raise TransportError("URL host is outside the registered source domains")
    return canonical


def _parse_url(url: str, allowed_domains: tuple[str, ...]) -> tuple[object, str, int, str]:
    if not isinstance(url, str) or len(url) > 4096:
        raise TransportError("URL is missing or too long")
    try:
        parsed = urlsplit(url)
        port = parsed.port or 443
    except ValueError as exc:
        raise TransportError("URL is malformed") from exc
    if parsed.scheme.lower() != "https":
        raise TransportError("only HTTPS URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise TransportError("URLs containing credentials are not allowed")
    if parsed.fragment:
        raise TransportError("URL fragments are not allowed")
    host = _check_scope(parsed.hostname or "", allowed_domains)
    if port != 443:
        raise TransportError("only the standard HTTPS port is allowed")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return parsed, host, port, path


def _resolve_public(host: str, port: int) -> tuple[str, ...]:
    try:
        answers = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise TransportError("DNS resolution failed") from exc
    addresses = tuple(dict.fromkeys(item[4][0] for item in answers))
    if not addresses:
        raise TransportError("DNS returned no addresses")
    checked: list[str] = []
    for value in addresses:
        try:
            address = ipaddress.ip_address(value.split("%", 1)[0])
        except ValueError as exc:
            raise TransportError("DNS returned an invalid address") from exc
        if not address.is_global:
            raise TransportError("DNS resolved to a non-public IP address")
        checked.append(str(address))
    return tuple(checked)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    """HTTPS connection that dials a validated IP but verifies the DNS host."""

    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host, port=port, timeout=timeout, context=ssl.create_default_context())
        self._pinned_address = address

    def connect(self) -> None:
        raw = socket.create_connection(
            (self._pinned_address, self.port), self.timeout, self.source_address
        )
        try:
            self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
        except Exception:
            raw.close()
            raise


class SafeHTTPSTransport:
    """Bounded HTTPS GET transport; no proxy, cookies, or automatic redirects."""

    _RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
    _REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

    def __init__(
        self,
        *,
        resolver: Callable[[str, int], tuple[str, ...]] = _resolve_public,
        connection_factory: Callable[[str, int, str, float], http.client.HTTPSConnection] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._resolver = resolver
        self._connection_factory = connection_factory or (
            lambda host, port, address, timeout: _PinnedHTTPSConnection(
                host, port, address, timeout
            )
        )
        self._sleep = sleep
        self._clock = clock

    def fetch(
        self,
        url: str,
        *,
        allowed_domains: tuple[str, ...],
        timeout_seconds: float = 10.0,
        max_bytes: int = 1_000_000,
        max_retries: int = 1,
        max_redirects: int = 3,
    ) -> TransportResponse:
        if not 0.1 <= timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 0.1 and 30")
        if not 1 <= max_bytes <= 1_000_000:
            raise ValueError("max_bytes must be between 1 and 1000000")
        if not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be between 0 and 2")
        if not 0 <= max_redirects <= 3:
            raise ValueError("max_redirects must be between 0 and 3")
        if not isinstance(allowed_domains, tuple) or not allowed_domains:
            raise ValueError("allowed_domains must be a non-empty tuple")

        requested_url = url
        current_url = url
        deadline = self._clock() + timeout_seconds
        redirects = 0
        attempts = 0
        while True:
            parsed, host, port, path = _parse_url(current_url, allowed_domains)
            addresses = self._resolver(host, port)
            if not addresses:
                raise TransportError("DNS returned no public address")
            response = None
            for retry in range(max_retries + 1):
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise TransportError("request deadline exceeded")
                attempts += 1
                try:
                    response = self._request_once(
                        host, port, addresses[0], path, remaining, max_bytes
                    )
                except (ssl.SSLError, TransportError):
                    raise
                except (OSError, TimeoutError, http.client.HTTPException) as exc:
                    if retry >= max_retries or self._clock() >= deadline:
                        raise TransportError("HTTPS request failed") from exc
                    delay = min(0.25 * (2 ** retry), max(0.5, deadline - self._clock()))
                    self._sleep(delay)
                    continue
                if response[0] in self._RETRY_STATUSES and retry < max_retries:
                    delay = min(0.25 * (2 ** retry), max(0.5, deadline - self._clock()))
                    self._sleep(delay)
                    continue
                break
            if response is None:
                raise TransportError("HTTPS request did not produce a response")

            status, headers, body = response
            if status in self._REDIRECT_STATUSES:
                location = headers.get("location")
                if not location:
                    raise TransportError("redirect response has no Location header")
                if redirects >= max_redirects:
                    raise TransportError("redirect limit exceeded")
                next_url = urljoin(current_url, location)
                _parse_url(next_url, allowed_domains)
                current_url = next_url
                redirects += 1
                continue

            return TransportResponse(
                requested_url=requested_url,
                final_url=current_url,
                status=status,
                headers=headers,
                body=body,
                attempts=attempts,
                redirects=redirects,
            )

    def _request_once(
        self, host: str, port: int, address: str, path: str,
        timeout: float, max_bytes: int,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = self._connection_factory(host, port, address, timeout)
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Host": host,
                    "User-Agent": "SarangshoPlusResearchBot/0.1 (+permission-governed)",
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, text/html;q=0.8",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            headers = {key.lower(): value for key, value in response.getheaders()}
            if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                raise TransportError("compressed responses are not accepted")
            content_length = headers.get("content-length")
            if content_length is not None:
                try:
                    if int(content_length) > max_bytes:
                        raise ResponseTooLarge("response exceeds configured byte limit")
                except ValueError as exc:
                    raise TransportError("invalid Content-Length header") from exc
            body = bytearray()
            while True:
                chunk = response.read(min(16384, max_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ResponseTooLarge("response exceeds configured byte limit")
            return response.status, headers, bytes(body)
        finally:
            connection.close()
