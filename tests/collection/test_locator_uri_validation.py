from __future__ import annotations

import pytest

from sngso_engine.domains import valid_http_uri


@pytest.mark.parametrize("uri", [
    "https://publisher.example.org/story/1",
    "http://publisher.example.org/feed.xml?edition=morning",
])
def test_valid_http_uri_accepts_absolute_http_and_https(uri):
    assert valid_http_uri(uri)


@pytest.mark.parametrize("uri", [
    "https://user:secret@publisher.example.org/story/1",
    "https://@publisher.example.org/story/1",
    "https://publisher.example.org/story/1#fragment",
    "https://publisher.example.org:99999/story/1",
    "https://publisher.example.org:bad/story/1",
    "https://publisher.example.org/story with spaces",
    "https://publisher.example.org\\\\@attacker.example/story/1",
    "https://127.0.0.1/story/1",
    "//publisher.example.org/story/1",
    "javascript:alert(1)",
])
def test_valid_http_uri_rejects_ambiguous_or_unsafe_uri(uri):
    assert valid_http_uri(uri) is False


def test_valid_http_uri_enforces_length_limit():
    assert valid_http_uri("https://publisher.example.org/" + "x" * 2048) is False
