from __future__ import annotations

import hashlib

import pytest

from sngso_engine.canonical import canonical_json_bytes
from sngso_engine.sqlite_converted_row_inspection import (
    SQLiteConvertedRowInspectionError,
    _encode_source_value,
    compare_converted_row_reports,
)


def test_source_encoder_uses_postgres_comparable_framing():
    assert _encode_source_value(None, "text_identity")[:1] == b"N"
    assert _encode_source_value("বাংলা", "text_identity")[:1] == b"T"
    assert _encode_source_value(-42, "integer_to_bigint")[:1] == b"I"
    assert _encode_source_value(1.25, "real_to_double_precision")[:1] == b"R"
    assert _encode_source_value(b"\x00\xff", "blob_to_bytea")[-2:] == b"\x00\xff"
    assert _encode_source_value('{"a":1}', "json_text_to_jsonb")[:1] == b"J"


@pytest.mark.parametrize("value", [True, 1.5, "1"])
def test_source_integer_conversion_rejects_non_integer_storage(value):
    with pytest.raises(SQLiteConvertedRowInspectionError):
        _encode_source_value(value, "integer_to_bigint")


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_source_float_conversion_rejects_non_finite_values(value):
    with pytest.raises(SQLiteConvertedRowInspectionError):
        _encode_source_value(value, "real_to_double_precision")


def test_source_integer_conversion_rejects_postgres_bigint_overflow():
    with pytest.raises(SQLiteConvertedRowInspectionError, match="bigint range"):
        _encode_source_value(2**63, "integer_to_bigint")


def test_comparison_requires_matching_source_and_mapping_pins():
    source = {
        "format": "sngso-sqlite-converted-row-proof-v1",
        "source_manifest_sha256": "a",
        "mapping_sha256": "b",
        "table_reports": [],
    }
    target = {
        "format": "sngso-postgres-mapped-row-inventory-v1",
        "source_manifest_sha256": "different",
        "mapping_sha256": "b",
        "table_reports": [],
    }
    source["report_sha256"] = hashlib.sha256(canonical_json_bytes(source)).hexdigest()
    target["report_sha256"] = hashlib.sha256(canonical_json_bytes(target)).hexdigest()
    with pytest.raises(SQLiteConvertedRowInspectionError, match="source_manifest_sha256"):
        compare_converted_row_reports(source, target)


def test_source_text_encoder_rejects_postgres_unrepresentable_nul():
    with pytest.raises(SQLiteConvertedRowInspectionError, match=r"U\+0000"):
        _encode_source_value("bad\x00text", "text_identity")
