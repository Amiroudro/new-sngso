import pytest

from sngso_engine.canonical import canonical_json_bytes, sha256_fingerprint


def test_object_key_order_does_not_change_fingerprint() -> None:
    assert sha256_fingerprint({"b": 2, "a": 1}) == sha256_fingerprint({"a": 1, "b": 2})


def test_unicode_is_preserved_in_canonical_utf8() -> None:
    assert "বাংলা".encode("utf-8") in canonical_json_bytes({"text": "বাংলা"})


def test_meaningful_content_change_changes_fingerprint() -> None:
    assert sha256_fingerprint({"headline": "A"}) != sha256_fingerprint({"headline": "B"})


def test_non_json_nan_is_rejected() -> None:
    with pytest.raises(ValueError):
        canonical_json_bytes({"invalid": float("nan")})
