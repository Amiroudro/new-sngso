import json
from pathlib import Path

from jsonschema import Draft202012Validator

from sngso_engine.contracts import validate_payload

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "contracts" / "v1" / "editorial-benchmark-identity-attestation.schema.json"


def payload():
    return {
        "attestation_id": "identity-attestation:review-001:1",
        "review_id": "review:sample-001",
        "review_revision": 1,
        "principal_id": "editor:sample-001",
        "issuer": "https://login.example.org/",
        "audience": "sngso-benchmark",
        "subject": "oidc-subject-001",
        "authenticated_at": "2026-11-20T00:00:00Z",
        "expires_at": "2026-11-20T01:00:00Z",
        "authentication_methods": ["pwd", "mfa"],
        "authentication_context": "urn:example:loa:2",
        "provider_key_id": "key-2026-01",
        "provider_adapter": "example-oidc-adapter/1.0",
        "session_fingerprint": "sha256:" + "a" * 64,
        "attested_at": "2026-11-20T00:01:00Z",
    }


def test_identity_attestation_schema_is_valid_draft_2020_12():
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
    Draft202012Validator.check_schema(schema)


def test_valid_provider_identity_attestation_passes_contract():
    validate_payload("editorial-benchmark-identity-attestation", payload())


def test_identity_attestation_rejects_missing_mfa_method_and_session_fingerprint():
    invalid = payload()
    invalid["authentication_methods"] = []
    invalid["session_fingerprint"] = "raw-session-secret"
    try:
        validate_payload("editorial-benchmark-identity-attestation", invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("invalid identity attestation must fail contract validation")
