"""Public-internet-only HTTPS transport with pinned DNS and bounded responses.

This module is deliberately separate from collection connectors. It is not
registered by the fixture orchestrator and does not enable live publisher
collection. A future adapter must be explicitly reviewed and wired in.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import ipaddress
import math
import random
import dns.exception
import dns.resolver
import socket
import ssl
import time
from typing import Callable, Mapping
from urllib.parse import urljoin, urlsplit


class TransportError(RuntimeError):
    """A request was rejected or could not be completed safely."""


class ResponseTooLarge(TransportError):
    """The response body exceeded the configured byte ceiling."""


def _valid_finite_number(value: object, minimum: float, maximum: float) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and minimum <= value <= maximum
    )


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
    raw_host = host[:-1] if host.endswith(".") else host
    if not raw_host or raw_host.endswith("."):
        raise TransportError("URL host is invalid")
    try:
        canonical = raw_host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise TransportError("URL host is invalid") from exc
    if not canonical or len(canonical) > 253:
        raise TransportError("URL host is invalid")
    labels = canonical.split(".")
    if any(
        not label
        or len(label) > 63
        or label.startswith("-")
        or label.endswith("-")
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789-" for char in label)
        for label in labels
    ):
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
    if any(
        char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F
        or ord(char) > 0x7E or char == chr(92)
        for char in url
    ):
        raise TransportError("URL must use visible ASCII or percent-encoded characters")
    if "#" in url:
        raise TransportError("URL fragments are not allowed")
    try:
        parsed = urlsplit(url)
        authority = parsed.netloc.rsplit("@", 1)[-1]
        if authority.endswith(":"):
            raise TransportError("URL port is empty")
        parsed_port = parsed.port
        port = 443 if parsed_port is None else parsed_port
        if ":" in authority and authority.rsplit(":", 1)[1] != "443":
            raise TransportError("only the standard HTTPS port is allowed")
    except ValueError as exc:
        raise TransportError("URL is malformed") from exc
    if parsed.scheme.lower() != "https":
        raise TransportError("only HTTPS URLs are allowed")
    if parsed.username is not None or parsed.password is not None:
        raise TransportError("URLs containing credentials are not allowed")
    host = _check_scope(parsed.hostname or "", allowed_domains)
    if port != 443:
        raise TransportError("only the standard HTTPS port is allowed")
    path = parsed.path or "/"
    if parsed.query:
        path += "?" + parsed.query
    return parsed, host, port, path


def _validate_public_addresses(values: object) -> tuple[str, ...]:
    """Fail closed for every resolver implementation, including injected adapters."""
    if not isinstance(values, (tuple, list)) or not values:
        raise TransportError("DNS returned no addresses")
    if len(values) > 16:
        raise TransportError("DNS returned too many addresses")
    checked: list[str] = []
    for value in dict.fromkeys(str(item) for item in values):
        if "%" in value:
            raise TransportError("DNS returned a scoped IP address")
        try:
            address = ipaddress.ip_address(value)
        except ValueError as exc:
            raise TransportError("DNS returned an invalid address") from exc
        if (
            not address.is_global
            or (
                isinstance(address, ipaddress.IPv6Address)
                and (address.ipv4_mapped is not None or address.teredo is not None or address.sixtofour is not None)
            )
        ):
            raise TransportError("DNS resolved to a non-public or transition IP address")
        checked.append(str(address))
    return tuple(checked)


class BoundedDNSResolver:
    """Resolve A and AAAA records within one shared DNS deadline."""

    def __init__(
        self,
        *,
        resolver_factory: Callable[[], object] | None = None,
        clock: Callable[[], float] = time.monotonic,
        max_timeout_seconds: float = 2.5,
    ) -> None:
        if not _valid_finite_number(max_timeout_seconds, 0.1, 10):
            raise ValueError("DNS timeout must be between 0.1 and 10 seconds")
        self._resolver_factory = resolver_factory or dns.resolver.Resolver
        self._clock = clock
        self._max_timeout_seconds = max_timeout_seconds

    def resolve(
        self, host: str, port: int, *, timeout_seconds: float
    ) -> tuple[str, ...]:
        if not _valid_finite_number(timeout_seconds, 0.1, 30):
            raise TransportError("DNS resolution has no usable time budget")
        deadline = self._clock() + min(timeout_seconds, self._max_timeout_seconds)
        resolver = self._resolver_factory()
        addresses: list[str] = []
        for record_type in ("A", "AAAA"):
            remaining = deadline - self._clock()
            if remaining <= 0:
                raise TransportError("DNS resolution deadline exceeded")
            try:
                answer = resolver.resolve(
                    host, record_type, lifetime=remaining, search=False
                )
            except dns.resolver.NoAnswer:
                continue
            except dns.exception.DNSException as exc:
                raise TransportError("bounded DNS resolution failed") from exc
            addresses.extend(
                str(record.address) for record in answer
                if getattr(record, "address", None)
            )
        if not addresses:
            raise TransportError("DNS returned no addresses")
        return _validate_public_addresses(addresses)


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
    _ALLOWED_CONTENT_TYPES = frozenset({
        "application/rss+xml",
        "application/atom+xml",
        "application/xml",
        "text/xml",
        "application/xhtml+xml",
        "text/html",
        "application/json",
    })

    def __init__(
        self,
        *,
        resolver: Callable[[str, int], tuple[str, ...]] | BoundedDNSResolver | None = None,
        connection_factory: Callable[[str, int, str, float], http.client.HTTPSConnection] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[float], float] = lambda ceiling: random.uniform(0.0, ceiling),
    ) -> None:
        self._resolver = resolver or BoundedDNSResolver(clock=clock)
        self._connection_factory = connection_factory or (
            lambda host, port, address, timeout: _PinnedHTTPSConnection(
                host, port, address, timeout
            )
        )
        self._sleep = sleep
        self._clock = clock
        self._jitter = jitter

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
        if not _valid_finite_number(timeout_seconds, 0.1, 30):
            raise ValueError("timeout_seconds must be between 0.1 and 30")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1 <= max_bytes <= 1_000_000:
            raise ValueError("max_bytes must be an integer between 1 and 1000000")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or not 0 <= max_retries <= 2:
            raise ValueError("max_retries must be an integer between 0 and 2")
        if isinstance(max_redirects, bool) or not isinstance(max_redirects, int) or not 0 <= max_redirects <= 3:
            raise ValueError("max_redirects must be an integer between 0 and 3")
        if not isinstance(allowed_domains, tuple) or not allowed_domains:
            raise ValueError("allowed_domains must be a non-empty tuple")

        requested_url = url
        current_url = url
        deadline = self._clock() + timeout_seconds
        redirects = 0
        attempts = 0
        while True:
            parsed, host, port, path = _parse_url(current_url, allowed_domains)
            remaining_for_dns = deadline - self._clock()
            if remaining_for_dns <= 0:
                raise TransportError("request deadline exceeded before DNS resolution")
            if isinstance(self._resolver, BoundedDNSResolver):
                addresses = self._resolver.resolve(
                    host, port, timeout_seconds=remaining_for_dns
                )
            else:
                addresses = self._resolver(host, port)
            # Revalidate at the transport boundary even when a custom resolver
            # is injected. Dependency injection must never bypass SSRF policy.
            addresses = _validate_public_addresses(addresses)
            response = None
            for retry in range(max_retries + 1):
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise TransportError("request deadline exceeded")
                attempts += 1
                try:
                    response = self._request_once(
                        host, port, addresses[0], path, remaining, max_bytes,
                        deadline,
                    )
                except (ssl.SSLError, TransportError):
                    raise
                except (OSError, TimeoutError, http.client.HTTPException) as exc:
                    if retry >= max_retries or self._clock() >= deadline:
                        raise TransportError("HTTPS request failed") from exc
                    delay = self._bounded_retry_delay(retry, deadline)
                    if delay > 0:
                        self._sleep(delay)
                    continue
                if response[0] in self._RETRY_STATUSES and retry < max_retries:
                    delay = self._bounded_retry_delay(retry, deadline)
                    if delay > 0:
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

    def _bounded_retry_delay(self, retry: int, deadline: float) -> float:
        remaining = deadline - self._clock()
        if remaining <= 0:
            return 0.0
        ceiling = min(0.25 * (2 ** retry), remaining)
        delay = self._jitter(ceiling)
        if not _valid_finite_number(delay, 0.0, ceiling):
            raise TransportError("retry jitter returned an invalid delay")
        return delay

    def _request_once(
        self, host: str, port: int, address: str, path: str,
        timeout: float, max_bytes: int, deadline: float,
    ) -> tuple[int, dict[str, str], bytes]:
        connection = self._connection_factory(host, port, address, timeout)
        try:
            connection.request(
                "GET",
                path,
                headers={
                    "Host": host,
                    "User-Agent": "SarangshoPlusResearchBot/0.1 (+permission-governed)",
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, application/xhtml+xml, text/html;q=0.8, application/json;q=0.7",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            remaining_for_headers = deadline - self._clock()
            if remaining_for_headers <= 0:
                raise TransportError("request deadline exceeded while sending request")
            active_socket = getattr(connection, "sock", None)
            if active_socket is not None:
                active_socket.settimeout(remaining_for_headers)
            response = connection.getresponse()
            raw_headers = response.getheaders()
            if len(raw_headers) > 64:
                raise TransportError("response contains too many headers")
            header_bytes = sum(
                len(str(key).encode("utf-8")) + len(str(value).encode("utf-8"))
                for key, value in raw_headers
            )
            if header_bytes > 16384:
                raise TransportError("response headers exceed configured byte limit")
            for singleton in ("content-type", "content-encoding", "transfer-encoding", "location"):
                values = [value for key, value in raw_headers if key.lower() == singleton]
                if len(values) > 1:
                    raise TransportError(f"duplicate {singleton} response headers")
            headers = {key.lower(): value for key, value in raw_headers}
            content_lengths = [
                value for key, value in raw_headers if key.lower() == "content-length"
            ]
            if len(content_lengths) > 1:
                raise TransportError("duplicate Content-Length headers")
            if content_lengths and any(not value.isdecimal() for value in content_lengths):
                raise TransportError("invalid Content-Length header")
            if "transfer-encoding" in headers and content_lengths:
                raise TransportError("ambiguous transfer and content length headers")
            transfer_encoding = headers.get("transfer-encoding", "").strip().lower()
            if transfer_encoding not in {"", "chunked"}:
                raise TransportError("unsupported transfer encoding")
            if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
                raise TransportError("compressed responses are not accepted")
            content_length = headers.get("content-length")
            if content_length is not None and (
                len(content_length) > len(str(max_bytes)) or int(content_length) > max_bytes
            ):
                raise ResponseTooLarge("response exceeds configured byte limit")
            if 200 <= response.status < 300 and response.status != 204:
                content_type = headers.get("content-type")
                media_type = content_type.split(";", 1)[0].strip().lower() if content_type else ""
                if media_type not in self._ALLOWED_CONTENT_TYPES:
                    raise TransportError("response Content-Type is missing or unsupported")
            body = bytearray()
            while True:
                remaining = deadline - self._clock()
                if remaining <= 0:
                    raise TransportError("request deadline exceeded")
                active_socket = getattr(connection, "sock", None)
                if active_socket is not None:
                    active_socket.settimeout(remaining)
                chunk = response.read(min(16384, max_bytes + 1 - len(body)))
                if not chunk:
                    break
                body.extend(chunk)
                if len(body) > max_bytes:
                    raise ResponseTooLarge("response exceeds configured byte limit")
            return response.status, headers, bytes(body)
        finally:
            connection.close()
