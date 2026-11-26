"""Refresh a mapping fingerprint without changing review status."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .postgres_mapping_skeleton import refresh_mapping_fingerprint
from .sqlite_export import write_manifest_new


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Recompute a schema-v36 mapping SHA-256 into a new file. "
            "This does not approve or validate the mapping."
        )
    )
    parser.add_argument("--mapping", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    try:
        mapping = json.loads(args.mapping.read_text(encoding="utf-8"))
        refreshed = refresh_mapping_fingerprint(mapping)
        write_manifest_new(args.output, refreshed)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"Mapping fingerprint was not written: {exc}", file=sys.stderr)
        return 2

    print(
        "Mapping fingerprint written to a new file. Review status was preserved; "
        "fingerprinting does not approve the mapping."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
