"""Ports that keep domain logic independent of storage technology."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol


class SourcePermissionRepository(Protocol):
    def get_current_for_source(
        self, source_id: str, *, at: datetime
    ) -> dict[str, Any] | None:
        """Return the applicable permission record, or None if none applies."""
        ...


class SourceObservationRepository(Protocol):
    def append_observation(self, observation: dict[str, Any]) -> None:
        """Append an immutable observation; never silently overwrite one."""
        ...


class EditionRepository(Protocol):
    def get_latest_published(self) -> dict[str, Any] | None:
        """Return the last known-good published edition, if one exists."""
        ...

    def publish_immutable(self, edition: dict[str, Any]) -> None:
        """Persist a new immutable edition snapshot."""
        ...
