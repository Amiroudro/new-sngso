from __future__ import annotations

import pytest

from sngso_engine.postgres_row_inspection import (
    PostgresRowInspectionError,
    _encode_target_value,
    inspect_postgres_mapped_rows,
)


def test_target_value_encoder_preserves_declared_conversion_types():
    assert _encode_target_value(None, "text_identity")[:1] == b"N"
    assert _encode_target_value("বাংলা", "text_identity")[:1] == b"T"
    assert _encode_target_value("-42", "integer_to_bigint")[:1] == b"I"
    assert _encode_target_value("1.25", "real_to_double_precision")[:1] == b"R"
    assert _encode_target_value("00ff", "blob_to_bytea")[-2:] == b"\x00\xff"
    assert _encode_target_value('{"a": 1}', "json_text_to_jsonb")[-8:] == b'{"a": 1}'


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-Infinity", "1e9999"])
def test_target_value_encoder_rejects_non_finite_float_values(value):
    with pytest.raises(PostgresRowInspectionError):
        _encode_target_value(value, "real_to_double_precision")


@pytest.mark.parametrize("value", ["01", "+1", "1.0", " 1", ""])
def test_target_value_encoder_rejects_noncanonical_bigint_values(value):
    with pytest.raises(PostgresRowInspectionError):
        _encode_target_value(value, "integer_to_bigint")


@pytest.mark.parametrize("value", ["0", "abc", "0xz1", "0 "])
def test_target_value_encoder_rejects_malformed_bytea_hex(value):
    with pytest.raises(PostgresRowInspectionError):
        _encode_target_value(value, "blob_to_bytea")


def test_target_value_encoder_rejects_unknown_conversion():
    with pytest.raises(PostgresRowInspectionError, match="unsupported conversion"):
        _encode_target_value("value", "guess")


def test_target_row_scan_requires_an_explicit_mapping_before_connection_access():
    with pytest.raises(PostgresRowInspectionError, match="approved mapping"):
        inspect_postgres_mapped_rows(
            object(), {}, {}, None,
        )


def test_target_row_scan_rejects_invalid_batch_size():
    with pytest.raises(PostgresRowInspectionError, match="batch_size"):
        inspect_postgres_mapped_rows(
            object(), {}, {}, {"format": "sngso-sqlite-to-postgres-mapping-v1"},
            batch_size=0,
        )
