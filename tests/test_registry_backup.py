from __future__ import annotations

from datetime import datetime, timedelta, timezone
import sqlite3

import pytest

from sngso_engine.operations import (
    OperationalReportError,
    backup_registry_database,
    restore_registry_backup,
    verify_registry_backup,
)
from sngso_engine.registry import SQLiteRegistry


def _add_run(registry):
    now = datetime.now(timezone.utc)
    record = {
        "schema_version": "1.0.0",
        "id": "pipeline-run:backup-fixture",
        "status": "running",
        "started_at": (now - timedelta(minutes=2)).isoformat(),
        "software_version": "backup-test/1.0",
        "config_hash": "sha256:" + "a" * 64,
        "input_hashes": ["sha256:" + "b" * 64],
        "initiated_by": "backup-test",
    }
    registry.add_pipeline_run(record, actor="test", reason="Create backup fixture.")


def test_backup_and_restore_preserve_schema_pipeline_runs_and_audit_chain(tmp_path):
    source = tmp_path / "registry.sqlite"
    backup = tmp_path / "registry-backup.sqlite"
    restored = tmp_path / "registry-restored.sqlite"

    with SQLiteRegistry(source) as registry:
        _add_run(registry)
        original_checkpoint = registry.audit_checkpoint()
        result = backup_registry_database(registry, backup)
        assert result["status"] == "verified"
        assert result["audit_chain_valid"] is True
        assert result["audit_checkpoint"] == original_checkpoint
        with pytest.raises(FileExistsError):
            backup_registry_database(registry, backup)

    verified = verify_registry_backup(backup, expected_schema_version=31)
    assert verified["integrity_check"] == "ok"
    assert verified["audit_chain_valid"] is True
    restored_result = restore_registry_backup(backup, restored)
    assert restored_result["status"] == "verified"
    with SQLiteRegistry(restored) as recovered:
        assert recovered.get_pipeline_run("pipeline-run:backup-fixture") is not None
        assert recovered.audit_checkpoint() == original_checkpoint
        assert recovered.verify_audit_chain() is True
        with pytest.raises(FileExistsError):
            restore_registry_backup(backup, restored)


def test_backup_verification_rejects_tampered_audit_chain_and_restore_fails_closed(tmp_path):
    source = tmp_path / "source.sqlite"
    backup = tmp_path / "tampered-backup.sqlite"
    destination = tmp_path / "must-not-restore.sqlite"
    with SQLiteRegistry(source) as registry:
        _add_run(registry)
        backup_registry_database(registry, backup)

    with sqlite3.connect(backup) as connection:
        connection.execute("DROP TRIGGER audit_events_no_update")
        connection.execute(
            "UPDATE audit_events SET event_hash = ? WHERE sequence = 1",
            ("sha256:" + "f" * 64,),
        )

    with pytest.raises(OperationalReportError, match="audit chain verification failed"):
        verify_registry_backup(backup)
    with pytest.raises(OperationalReportError, match="audit chain verification failed"):
        restore_registry_backup(backup, destination)
    assert not destination.exists()


def test_backup_rejects_active_transaction_and_does_not_create_partial_file(tmp_path):
    backup = tmp_path / "unsafe.sqlite"
    with SQLiteRegistry() as registry:
        with registry._transaction():
            with pytest.raises(OperationalReportError, match="active registry transaction"):
                backup_registry_database(registry, backup)
        assert not backup.exists()
