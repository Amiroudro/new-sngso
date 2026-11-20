import pytest

from sngso_engine.registry import RegistryIntegrityError, SQLiteRegistry


def _principal(registry, principal_id, roles, *, kind="human"):
    return registry.register_benchmark_principal(
        principal_id,
        display_name=principal_id,
        roles=roles,
        provisioned_by="offline-deployment-operator",
        reason="Controlled test bootstrap",
        languages=["bn"] if "benchmark_reviewer" in roles else [],
        principal_kind=kind,
    )


def test_security_admin_role_can_only_be_assigned_from_allowed_registry_roles():
    with SQLiteRegistry() as registry:
        admin = _principal(registry, "admin:one", ["benchmark_security_admin"])
        assert admin["roles"] == ["benchmark_security_admin"]
        with pytest.raises(ValueError, match="roles must be"):
            _principal(registry, "admin:bad", ["root"])


def test_principal_revocation_and_reinstatement_are_explicit_and_audited():
    with SQLiteRegistry() as registry:
        _principal(registry, "admin:one", ["benchmark_security_admin"])
        _principal(registry, "reviewer:one", ["benchmark_reviewer"])
        revoked = registry.revoke_benchmark_principal(
            "reviewer:one", actor="admin:one", reason="Annual access review",
        )
        assert revoked["active"] is False
        restored = registry.reinstate_benchmark_principal(
            "reviewer:one", actor="admin:one", reason="Qualification reconfirmed",
        )
        assert restored["active"] is True
        with pytest.raises(RegistryIntegrityError, match="already active"):
            registry.reinstate_benchmark_principal(
                "reviewer:one", actor="admin:one", reason="Duplicate restore",
            )
        assert restored["roles"] == ["benchmark_reviewer"]
        assert restored["languages"] == ["bn"]


def test_unknown_principal_cannot_be_reinstated():
    with SQLiteRegistry() as registry:
        with pytest.raises(RegistryIntegrityError, match="unknown"):
            registry.reinstate_benchmark_principal(
                "missing", actor="admin:one", reason="Unknown identity",
            )
