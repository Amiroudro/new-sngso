from __future__ import annotations

import pytest

from sngso_engine import transport


class FakeResponse:
    def __init__(self, status=200, headers=None, body=b"ok"):
        self.status = status
        self._headers = list(
            [("Content-Type", "application/rss+xml; charset=utf-8")]
            if headers is None and 200 <= status < 300 and status != 204
            else (headers or ())
        )
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
    "https://news.example.org:0/feed.xml",
    "https://news.example.org:/feed.xml",
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


def test_bounded_dns_resolver_checks_both_address_families_and_public_ip_scope():
    class Record:
        def __init__(self, address):
            self.address = address

    class Resolver:
        def __init__(self):
            self.calls = []
        def resolve(self, host, record_type, *, lifetime, search):
            self.calls.append((host, record_type, lifetime, search))
            if record_type == "A":
                return [Record("93.184.216.34")]
            raise transport.dns.resolver.NoAnswer

    instance = Resolver()
    resolver = transport.BoundedDNSResolver(
        resolver_factory=lambda: instance,
        clock=lambda: 10.0,
        max_timeout_seconds=2.0,
    )
    assert resolver.resolve("news.example.org", 443, timeout_seconds=5.0) == (
        "93.184.216.34",
    )
    assert [call[1] for call in instance.calls] == ["A", "AAAA"]
    assert all(call[2] <= 2.0 and call[3] is False for call in instance.calls)


def test_bounded_dns_resolver_rejects_private_answer():
    class Record:
        address = "10.1.2.3"

    class Resolver:
        def resolve(self, host, record_type, *, lifetime, search):
            if record_type == "A":
                return [Record()]
            raise transport.dns.resolver.NoAnswer

    resolver = transport.BoundedDNSResolver(
        resolver_factory=Resolver,
        clock=lambda: 1.0,
    )
    with pytest.raises(transport.TransportError, match="non-public"):
        resolver.resolve("news.example.org", 443, timeout_seconds=3.0)


def test_bounded_dns_resolver_uses_one_deadline_for_a_and_aaaa():
    class Record:
        address = "93.184.216.34"

    now = [0.0]

    class Resolver:
        def resolve(self, host, record_type, *, lifetime, search):
            if record_type == "A":
                now[0] = 1.1
                return [Record()]
            raise transport.dns.resolver.NoAnswer

    resolver = transport.BoundedDNSResolver(
        resolver_factory=Resolver,
        clock=lambda: now[0],
        max_timeout_seconds=1.0,
    )
    with pytest.raises(transport.TransportError, match="deadline exceeded"):
        resolver.resolve("news.example.org", 443, timeout_seconds=1.0)


def test_transport_accepts_supported_content_type_with_parameters():
    client, _ = make_transport([
        FakeResponse(headers=[("Content-Type", "application/atom+xml; charset=utf-8")])
    ])
    result = client.fetch(
        "https://news.example.org/feed.xml",
        allowed_domains=("example.org",),
    )
    assert result.status == 200


def test_transport_rejects_missing_or_unsupported_content_type():
    for headers in ([], [("Content-Type", "text/plain")]):
        client, _ = make_transport([FakeResponse(headers=headers)])
        with pytest.raises(transport.TransportError, match="Content-Type"):
            client.fetch(
                "https://news.example.org/feed.xml",
                allowed_domains=("example.org",),
            )


def test_transport_rejects_excessive_response_header_count():
    headers = [("X-Header-" + str(i), "v") for i in range(65)]
    headers.append(("Content-Type", "application/rss+xml"))
    client, _ = make_transport([FakeResponse(headers=headers)])
    with pytest.raises(transport.TransportError, match="too many headers"):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org",),
        )


def test_transport_rejects_excessive_response_header_bytes():
    headers = [
        ("Content-Type", "application/rss+xml"),
        ("X-Large", "x" * 17000),
    ]
    client, _ = make_transport([FakeResponse(headers=headers)])
    with pytest.raises(transport.TransportError, match="header.*byte limit"):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org",),
        )


def test_transport_rejects_conflicting_or_ambiguous_length_headers():
    cases = [
        [("Content-Type", "application/rss+xml"),
         ("Content-Length", "10"), ("Content-Length", "11")],
        [("Content-Type", "application/rss+xml"),
         ("Content-Length", "10"), ("Transfer-Encoding", "chunked")],
    ]
    for headers in cases:
        client, _ = make_transport([FakeResponse(headers=headers)])
        with pytest.raises(transport.TransportError, match="Content-Length|transfer"):
            client.fetch(
                "https://news.example.org/feed.xml",
                allowed_domains=("example.org",),
            )



@pytest.mark.parametrize("addresses", [
    ("127.0.0.1",),
    ("10.0.0.8", "93.184.216.34"),
    ("fd00::1",),
    ("not-an-ip",),
])
def test_transport_revalidates_all_injected_resolver_addresses(addresses):
    connected = []
    client = transport.SafeHTTPSTransport(
        resolver=lambda host, port: addresses,
        connection_factory=lambda *args: connected.append(args)
        or pytest.fail("private or malformed DNS answer must never connect"),
    )
    with pytest.raises(transport.TransportError):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org",),
        )
    assert connected == []



@pytest.mark.parametrize("address", [
    "93.184.216.34%eth0",
    "2001:4860:4860::8888%eth0",
])
def test_transport_rejects_scoped_dns_addresses(address):
    connected = []
    client = transport.SafeHTTPSTransport(
        resolver=lambda host, port: (address,),
        connection_factory=lambda *args: connected.append(args)
        or pytest.fail("scoped DNS address must never connect"),
    )
    with pytest.raises(transport.TransportError, match="scoped IP"):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org",),
        )
    assert connected == []



@pytest.mark.parametrize("headers", [
    [
        ("Content-Type", "application/rss+xml"),
        ("content-type", "text/html"),
    ],
    [
        ("Content-Encoding", "identity"),
        ("content-encoding", "gzip"),
    ],
    [
        ("Location", "https://news.example.org/a"),
        ("location", "https://news.example.org/b"),
    ],
])
def test_transport_rejects_duplicate_singleton_headers(headers):
    client, connections = make_transport([FakeResponse(302, headers)])
    with pytest.raises(transport.TransportError, match="duplicate"):
        client.fetch(
            "https://news.example.org/feed.xml",
            allowed_domains=("example.org",),
        )
    assert len(connections) == 1



def test_pinned_connection_dials_ip_but_uses_original_host_for_tls(monkeypatch):
    captured = {}
    raw_socket = object()

    class FakeTLSContext:
        def wrap_socket(self, raw, *, server_hostname):
            captured["raw"] = raw
            captured["server_hostname"] = server_hostname
            return "wrapped-tls-socket"

    def fake_create_connection(address, timeout, source_address):
        captured["address"] = address
        captured["timeout"] = timeout
        return raw_socket

    monkeypatch.setattr(
        transport.ssl, "create_default_context", lambda: FakeTLSContext()
    )
    monkeypatch.setattr(transport.socket, "create_connection", fake_create_connection)

    connection = transport._PinnedHTTPSConnection(
        "news.example.org", 443, "93.184.216.34", 4.0
    )
    connection.connect()

    assert captured == {
        "address": ("93.184.216.34", 443),
        "timeout": 4.0,
        "raw": raw_socket,
        "server_hostname": "news.example.org",
    }
    assert connection.sock == "wrapped-tls-socket"



def test_remaining_deadline_is_applied_before_response_headers():
    state = {"now": 0.0, "timeouts": []}

    class TimedSocket:
        def settimeout(self, value):
            state["timeouts"].append(value)

    class DelayedRequestConnection(FakeConnection):
        def __init__(self):
            super().__init__(FakeResponse(body=b"feed"))
            self.sock = TimedSocket()

        def request(self, method, path, headers):
            state["now"] = 3.0

        def getresponse(self):
            assert state["timeouts"][-1] == pytest.approx(2.0)
            return self.response

    connection = DelayedRequestConnection()
    client = transport.SafeHTTPSTransport(
        clock=lambda: state["now"],
        connection_factory=lambda *args: connection,
    )

    status, headers, body = client._request_once(
        "news.example.org", 443, "93.184.216.34", "/feed.xml",
        5.0, 100, 5.0,
    )

    assert status == 200
    assert body == b"feed"
    assert state["timeouts"][0] == pytest.approx(2.0)
