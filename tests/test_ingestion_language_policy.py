"""Regression tests for endpoint language declaration matching."""

import pytest

from sngso_engine.ingestion import IngestionError, language_is_supported


@pytest.mark.parametrize(
    ("language", "declared", "expected"),
    [
        ("en-US", ["en"], True),
        ("en-GB", ["en"], True),
        ("en-US", ["en-US"], True),
        ("en-us", ["en-US"], True),
        ("en-GB", ["en-US"], False),
        ("en", ["en-US"], False),
        ("bn-BD", ["bn"], True),
        ("bn-IN", ["bn-BD"], False),
        ("en-GB", ["bn", "en-US"], False),
        ("en-GB", ["bn", "en"], True),
    ],
)
def test_language_declaration_matching_is_specific_and_consistent(language, declared, expected):
    assert language_is_supported(language, declared) is expected


@pytest.mark.parametrize(
    ("language", "declared"),
    [
        ("not a language", ["en"]),
        ("en", ["not a language"]),
        ("en", "en"),
        ("en", [None]),
    ],
)
def test_invalid_language_declarations_fail_closed(language, declared):
    with pytest.raises(IngestionError):
        language_is_supported(language, declared)
