"""Restore a SQLite backup to a new path after verifying the exact recorded dataset."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .operations import restore_registry_backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("expected a reference manifest object")
        result = restore_registry_backup(args.backup, args.destination, reference_manifest=manifest)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Restore stopped: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
