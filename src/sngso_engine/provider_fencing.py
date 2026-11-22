"""Provider-boundary fencing contract for side-effecting stage adapters.

This module transports lease generations and deterministic idempotency identity
to a provider adapter. It does not emulate provider-side atomic fencing: the
remote system must reject stale generations and deduplicate keys itself.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Mapping, Protocol, runtime_checkable

from .canonical import sha256_fingerprint
from .worker_leases import WorkerLease, WorkerLeaseStore


class ProviderFenceError(RuntimeError):
    """A provider command cannot be safely fenced or dispatched."""


@dataclass(frozen=True)
class ProviderCommand:
    """Immutable identity that a provider must atomically enforce."""

    provider_namespace: str
    pipeline_run_id: str
    stage_ordinal: int
    idempotency_key: str
    fencing_token: int
    request_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.provider_namespace, str) or not re.fullmatch(
            r"[a-z][a-z0-9_.-]{0,99}", self.provider_namespace
        ):
            raise ValueError("provider_namespace must be a bounded lowercase identifier")
        if not isinstance(self.pipeline_run_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_.:-]{1,160}", self.pipeline_run_id
        ):
            raise ValueError("pipeline_run_id must be a bounded identifier")
        if isinstance(self.stage_ordinal, bool) or not isinstance(self.stage_ordinal, int) or self.stage_ordinal < 0:
            raise ValueError("stage_ordinal must be a non-negative integer")
        if not isinstance(self.idempotency_key, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.idempotency_key
        ):
            raise ValueError("idempotency_key must be a SHA-256 fingerprint")
        if isinstance(self.fencing_token, bool) or not isinstance(self.fencing_token, int) or self.fencing_token < 1:
            raise ValueError("fencing_token must be a positive integer")
        if not isinstance(self.request_fingerprint, str) or not re.fullmatch(
            r"sha256:[0-9a-f]{64}", self.request_fingerprint
        ):
            raise ValueError("request_fingerprint must be a SHA-256 fingerprint")


@runtime_checkable
class FencedProviderAdapter(Protocol):
    """Provider client contract.

    Implementations must atomically reject commands whose fencing_token is
    lower than the highest accepted generation for the protected resource.
    They must also bind idempotency_key to request_fingerprint and return the
    original result for an exact duplicate. A key reused with another request
    fingerprint must be rejected. These checks belong inside the provider's
    serialization/transaction boundary, not in this Python wrapper.
    """

    def execute_fenced(self, command: ProviderCommand, request: Mapping[str, Any]) -> Any:
        ...


def execute_fenced_provider_command(
    provider: FencedProviderAdapter,
    lease_store: WorkerLeaseStore,
    lease: WorkerLease,
    *,
    provider_namespace: str,
    stage_ordinal: int,
    idempotency_key: str,
    request: Mapping[str, Any],
) -> Any:
    """Dispatch one command with lease validation and provider-enforced fence data.

    The preflight lease check prevents obviously stale workers from dispatching.
    The provider must still atomically enforce the generation because lease
    ownership can change between this check and the remote operation.
    A failed postflight check means the remote outcome is uncertain and must be
    reconciled before any retry.
    """
    if not isinstance(provider, FencedProviderAdapter):
        raise TypeError("provider must implement execute_fenced")
    if not isinstance(lease_store, WorkerLeaseStore) or not isinstance(lease, WorkerLease):
        raise TypeError("lease_store and lease must be a WorkerLeaseStore and WorkerLease")
    if not isinstance(request, Mapping):
        raise TypeError("provider request must be a mapping")
    request_fingerprint = sha256_fingerprint(dict(request))
    command = ProviderCommand(
        provider_namespace=provider_namespace,
        pipeline_run_id=lease.pipeline_run_id,
        stage_ordinal=stage_ordinal,
        idempotency_key=idempotency_key,
        fencing_token=lease.fencing_token,
        request_fingerprint=request_fingerprint,
    )
    lease_store.assert_current(lease)
    result = provider.execute_fenced(command, request)
    # If ownership changed while the provider call was in flight, do not let
    # this worker proceed to local checkpoint success. Reconcile remotely.
    lease_store.assert_current(lease)
    return result
