"""Runtime validation for versioned JSON contracts."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

CONTRACTS_DIR = Path(__file__).resolve().parents[2] / "contracts" / "v1"


class ContractValidationError(ValueError):
    """Raised when a payload does not conform to its versioned contract."""

    def __init__(self, contract_name: str, messages: list[str]) -> None:
        self.contract_name = contract_name
        self.messages = messages
        super().__init__(f"{contract_name}: " + "; ".join(messages))


@lru_cache(maxsize=1)
def _registry() -> Registry:
    common_path = CONTRACTS_DIR / "common.schema.json"
    common = json.loads(common_path.read_text(encoding="utf-8"))
    return Registry().with_resource(
        common["$id"], Resource.from_contents(common)
    )


@lru_cache(maxsize=32)
def load_schema(contract_name: str) -> dict[str, Any]:
    """Load a known v1 schema by its filename stem."""
    if not contract_name or "/" in contract_name or "\\" in contract_name:
        raise ValueError("contract_name must be a schema filename stem")
    path = CONTRACTS_DIR / f"{contract_name}.schema.json"
    if not path.is_file() or path.parent != CONTRACTS_DIR:
        raise FileNotFoundError(f"Unknown contract: {contract_name}")
    schema = json.loads(path.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return schema


def validate_payload(contract_name: str, payload: dict[str, Any]) -> None:
    """Validate a payload, raising a concise error for all violations."""
    validator = Draft202012Validator(
        load_schema(contract_name),
        registry=_registry(),
        format_checker=FormatChecker(),
    )
    errors = sorted(
        validator.iter_errors(payload),
        key=lambda error: (list(error.absolute_path), error.message),
    )
    if errors:
        messages = [
            f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: "
            f"{error.message}"
            for error in errors
        ]
        raise ContractValidationError(contract_name, messages)
