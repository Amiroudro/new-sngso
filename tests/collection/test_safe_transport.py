from __future__ import annotations

import pytest

from sngso_engine import transport


class FakeResponse:
    def __init__(self, status=200, headers=(), body=b"ok"):
        self.status = status
        self._headers = list(headers)
        self._body = body

    def getheaders(self):
        return self._headers

    def read(self, amount=-1):
        if not self._body:
            return b""
        chunk, self._body = self._body[:amount], self._body[amount:]
        return chunk


class FakeConnection:
    def __init__(self, response):
        self.response = response
        self.request_args = None
        self.closed = False

    def request(self, method, path, headers):
        self.request_args = (method, path, headers)

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


def make_transport(responses, seen=None):
    connections = []
    def factory(host, port, address, timeout):
        connection = FakeConnection(responses.pop(0))
        connections.append((host, port, address, timeout, connection))
        return connection
    return (
        transport.SafeHTTPSTransport(
            resolver=lambda host, port: ("93.184.216.34",),
            connection_factory=factory,
            sleep=lambda seconds: None,
        ),
        connections,
    )


def test_https_transport_pins_resolved_ip_and_preserves_host_header():
    client, connections = make_transport([FakeResponse(body=b"feed")])
    result = client.fetch(
        "https://news.example.org/feed.xml",
        allowed_domains=("example.org",),
    )
    host, port, address, timeout, connection = connections[0]
    assert result.body == b"feed"
    assert result.status == 200
    assert (host, port, address) == ("news.example.org", 443, "93.184.216.34")
    assert connection.request_args[0:2] == ("GET", "/feed.xml")
    assert connection.request_args[2]["Host"] == "news.example.org"
    assert connection.request_args[2]["Accept-Encoding"] == "identity"
    assert connection.closed


@pytest.mark.parametrize("url", [
    "http://news.example.org/feed.xml",
    "https://unrelated.example.net/feed.xml",
    "https://user:pass@news.example.org/feed.xml",
    "https://news.example.org:8443/feed.xml",
    "https://127.0.0.1/feed.xml",
])
def test_transport_rejects_unsafe_url_before_dns(url):
    called = []
    client = transport.SafeHTTPSTransport(
        resolver=lambda host, port: called.append(host) or ("93.184.216.34",),
        connection_factory=lambda *args: pytest.fail("must not connect"),
    )
    with pytest.raises(transport.TransportError):
        client.fetch(url, allowed_domains=("example.org",))
    assert called == []


def test_redirect_cannot_escape_source_domain():
    client, connections = make_transport([
        FakeResponse(302, [("Location", "https://other.example.net/article")])
    ])
    with pytest.raises(transport.TransportError, match="outside the registered source domains"):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org",),
        )
    assert len(connections) == 1


def test_redirect_cannot_downgrade_to_http():
    client, _ = make_transport([
        FakeResponse(302, [("Location", "http://news.example.org/article")])
    ])
    with pytest.raises(transport.TransportError, match="only HTTPS"):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org",),
        )


def test_response_body_limit_is_enforced_while_streaming():
    client, _ = make_transport([FakeResponse(body=b"123456789")])
    with pytest.raises(transport.ResponseTooLarge):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org",),
            max_bytes=8,
        )


def test_declared_content_length_over_limit_is_rejected_before_body_read():
    client, _ = make_transport([
        FakeResponse(headers=[("Content-Length", "1000")], body=b"")
    ])
    with pytest.raises(transport.ResponseTooLarge):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org",),
            max_bytes=100,
        )


def test_compressed_response_is_rejected_to_avoid_decompression_bombs():
    client, _ = make_transport([
        FakeResponse(headers=[("Content-Encoding", "gzip")], body=b"compressed")
    ])
    with pytest.raises(transport.TransportError, match="compressed responses"):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org",),
        )


def test_transient_status_retries_within_budget():
    client, connections = make_transport([
        FakeResponse(503, body=b"temporary"),
        FakeResponse(200, body=b"recovered"),
    ])
    result = client.fetch(
        "https://news.example.org/feed.xml",
        allowed_domains=("example.org",),
        max_retries=1,
    )
    assert result.status == 200
    assert result.body == b"recovered"
    assert result.attempts == 2
    assert len(connections) == 2


def test_redirect_count_is_bounded():
    client, _ = make_transport([
        FakeResponse(302, [("Location", "/one")]),
        FakeResponse(302, [("Location", "/two")]),
    ])
    with pytest.raises(transport.TransportError, match="redirect limit"):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org",),
            max_redirects=1,
        )


def test_dns_rejects_private_and_link_local_addresses(monkeypatch):
    monkeypatch.setattr(
        transport.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("127.0.0.1", 443)),
        ],
    )
    with pytest.raises(transport.TransportError, match="non-public"):
        transport._resolve_public("news.example.org", 443)


def test_dns_requires_every_returned_address_to_be_public(monkeypatch):
    monkeypatch.setattr(
        transport.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.8", 443)),
        ],
    )
    with pytest.raises(transport.TransportError, match="non-public"):
        transport._resolve_public("news.example.org", 443)


def test_transport_requires_a_finite_deadline_and_bounded_budgets():
    client, _ = make_transport([FakeResponse()])
    with pytest.raises(ValueError, match="timeout_seconds"):
        client.fetch("https://news.example.org/", allowed_domains=("example.org",), timeout_seconds=60)
    with pytest.raises(ValueError, match="max_bytes"):
        client.fetch("https://news.example.org/", allowed_domains=("example.org",), max_bytes=2_000_000)


@pytest.mark.parametrize("url", [
    "https://bad..example.org/feed.xml",
    "https://-bad.example.org/feed.xml",
    "https://bad_.example.org/feed.xml",
])
def test_transport_rejects_malformed_dns_labels(url):
    called = []
    client = transport.SafeHTTPSTransport(
        resolver=lambda host, port: called.append(host) or ("93.184.216.34",),
        connection_factory=lambda *args: pytest.fail("must not connect"),
    )
    with pytest.raises(transport.TransportError, match="invalid"):
        client.fetch(url, allowed_domains=("example.org",))
    assert called == []


def test_transport_rejects_malformed_registered_domain():
    client = transport.SafeHTTPSTransport(
        resolver=lambda *args: pytest.fail("must reject allowlist before DNS"),
        connection_factory=lambda *args: pytest.fail("must not connect"),
    )
    with pytest.raises(transport.TransportError, match="invalid"):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org/path",),
        )
