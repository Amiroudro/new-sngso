"""Create a validated schema-v36 SQLite audit checkpoint in a new local file."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

from .audit_checkpoint import build_audit_checkpoint_record
from .sqlite_export import build_sqlite_export_manifest, write_manifest_new


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Verify the schema-v36 SQLite source and write its audit tip to a "
            "new checkpoint file. Store that file outside the source's trust boundary."
        )
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        manifest = build_sqlite_export_manifest(args.source)
        record = build_audit_checkpoint_record(
            manifest,
            generated_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        )
        destination = write_manifest_new(args.output, record)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Checkpoint export stopped safely: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({
        "status": "checkpoint_created",
        "checkpoint_sha256": record["checkpoint_sha256"],
        "sequence": record["audit_checkpoint"]["sequence"],
        "event_hash": record["audit_checkpoint"]["event_hash"],
        "output": str(destination),
        "database_mutated": False,
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
