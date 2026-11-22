# Section 9.13 implementation note: bounded POSIX worker subprocesses

Status: **local POSIX subprocess supervision, lease-aware process-control integration, provider reconciliation reference checks, and lease-loss crash-window coverage implemented; full-suite CI verified. Production provider and operational acceptance remain open.**

## Implemented API

`sngso_engine.worker_supervisor` provides:

- `WorkerEntrypoint`: a statically configured name, immutable argv tuple, explicit environment mapping, and JSON Schemas for request and result objects.
- `WorkerSupervisor`: a POSIX-only process-group supervisor with a bounded wall-clock deadline, graceful SIGTERM, bounded grace period, and SIGKILL escalation.
- `WorkerProcessResult`: bounded outcome metadata. It does not expose captured stderr or raw diagnostic output.
- `supervised_checkpoint_handler`: adapts a validated worker result into an `execute_checkpointed_stage` handler. Only a successful child result becomes stage output; timeout, cancellation, lease loss, or protocol failure raises so a non-replay-safe checkpoint remains unresolved.

Requests are validated against the registered entrypoint's JSON Schema and byte limit, then delivered on stdin. Request data is never interpolated into argv. The process is launched with `shell=False`, `close_fds=True`, a new POSIX session/process group, and only the explicitly configured environment. No ambient environment is inherited.

stdout and stderr are drained concurrently so a full pipe cannot deadlock the child. The supervisor retains at most the configured byte ceilings, marks truncation, and rejects truncated stdout as a valid result. A child result must be a versioned JSON envelope and its result object must pass the entrypoint's result schema. Diagnostic bytes are not returned or persisted by this library.

The process group is also terminated when the main child exits but a descendant keeps inherited pipes open. Cleanup waits are bounded; if cleanup cannot be confirmed, the outcome is failed rather than reported as successful.

## Deliberate limitations

- POSIX only. Construction fails closed on Windows until equivalent process-group semantics are implemented and tested there.
- The API launches only statically registered argv vectors. It does not import arbitrary Python callables or accept shell commands.
- This is a local process primitive, not a daemon, distributed supervisor, service manager, or worker pool.
- `WorkerSupervisor.run` now accepts a started `WorkerLeaseSession`, verifies lease ownership before launch, and passes the lease envelope and fencing generation through the private control descriptor. A parent-side polling loop observes heartbeat loss and durable cancellation while the child runs. The child receives cancellation on a separate descriptor. A result is classified as `cancelled` only when the child explicitly returns `cancellation_acknowledged: true`; otherwise it is `cancelled_unacknowledged`. The supervisor never acknowledges cancellation in the registry on the child's behalf. The caller remains responsible for session lifecycle and for checkpoint reconciliation after any non-success outcome. The checkpoint stage runner now revalidates the lease in the handler-exception path before recording failure/indeterminate status; if ownership was lost, the stale worker cannot mutate the checkpoint and the `executing` state remains for reconciliation.
- Provider fencing, idempotency, and reconciliation remain mandatory before replaying an interrupted side effect.
- `sngso_engine.worker_control` supplies the bounded lease envelope over an inherited private pipe and a separate one-byte cancellation pipe. Descriptor numbers may be passed as environment values; bearer tokens are never passed in argv, request JSON, environment values, or output.
- The supervisor does not release or acknowledge the lease automatically, and does not mutate checkpoint state. Callers must retain session ownership and reconcile uncertain child outcomes before retry.
- CPU/memory/file-descriptor resource limits, external signal orchestration, supervisor restart recovery, and provider-enforced fencing remain outside this implementation.
- The supervisor does not trigger collection, scheduling, publication, release, or deployment.

## Test coverage added

Tests cover successful stdin/stdout protocol, static entrypoint allowlisting, request/result schema rejection, nonzero exits, timeout escalation, bounded stdout/stderr draining, environment isolation, and descendant cleanup. Lease-aware tests cover private-FD credential handoff, heartbeat renewal during child execution, durable cancellation propagation, explicit acknowledgement versus unacknowledged cancellation, and child termination after lease loss, including a point-of-use lease check that raises after launch. Checkpoint integration tests prove successful validated child output commits only its fingerprint and timeout leaves a side-effecting checkpoint indeterminate; a stale worker cannot change checkpoint state after lease loss. Protected-IPC unit tests cover envelope validation, token isolation, cancellation signaling, descriptor cleanup, and bounded polling.

## Acceptance still required

Before production use: make the worker validate lease ownership and fencing at each side-effect boundary; connect child outcomes to checkpoint reconciliation without replaying uncertain work; add platform-tested CPU/memory/FD limits; add supervisor signal/shutdown orchestration and restart recovery; test stale fencing at provider boundaries and crash windows; provide external monitoring, shared durable lease storage, backup/restore, and an explicit operator release decision.

No live worker is enabled by this module. No scheduler activation, collection, publication, release, merge, or deployment is authorized.


## Verification

Latest full Python 3.12 regression suite: **974 passed in 14.13 seconds** in GitHub Actions [run #45031050579](https://github.com/Amiroudro/new-sngso/actions/runs/45031050579), on exact PR head `94f1f236368b99e2db66250272cc7f80826457a2`. This run includes provider reconciliation reference-drift rejection, malformed-reference handling, and the remote-completion/lease-loss crash-window tests. It verifies local POSIX subprocess supervision and local contract behavior, not a concrete production provider or production process management.


## Pending-provider reconciliation handoff

A supervised child can exit without a definitive local result while the remote
provider operation continues. The checkpoint layer now records the first
provider-confirmed `pending` response as an `indeterminate` checkpoint and
stores the provider's bounded opaque operation reference in the append-only
revision history. The next reconciliation call receives both the deterministic
idempotency key and that persisted operation reference.

A pending result never invokes the stage handler. A later provider-confirmed
completion records success without launching another child; provider-confirmed
`not_started` is still the only result that can authorize a side-effecting
retry. Repeated pending polls do not rewrite an already-indeterminate head, so
the original provider reference remains immutable. Provider adapters must
therefore return a stable operation reference on the first pending response.

The provider's free-form metadata is deliberately not persisted. Only the
existing allow-listed operational tokens are recorded. This is a local
reconciliation handoff, not a provider adapter or proof of remote idempotency.


## Provider-boundary fencing command contract

`sngso_engine.provider_fencing` now defines an immutable `ProviderCommand`
that binds provider namespace, pipeline run, stage ordinal, deterministic
idempotency key, lease fencing generation, and request fingerprint. The
`execute_fenced_provider_command` wrapper checks current lease ownership before
dispatch and again after the provider returns. If ownership is lost in flight,
the wrapper refuses to return success to the local caller; the remote result
must be reconciled.

The `fenced_provider_stage_handler` factory adapts this contract to the existing checkpoint handler signature: it builds a bounded request from in-memory stage context and forwards the checkpoint runner's deterministic idempotency key plus the captured lease generation. The checkpoint runner can retain its own point-of-use `lease_guard` as an additional local check. The `FencedProviderAdapter` protocol requires the provider implementation to
atomically enforce two rules inside its own serialization boundary:

1. Reject a command whose fencing generation is lower than the highest
   generation already accepted for the protected resource.
2. Bind an idempotency key to its request fingerprint, returning the original
   result for an exact duplicate and rejecting reuse with a different request.

The preflight SQLite lease check is not a substitute for provider enforcement:
ownership can change between the check and the remote operation. The provider
must implement the atomic comparison itself. Tests use a deliberately small
fake provider to verify the contract, stale-generation rejection, duplicate
deduplication, conflicting-payload rejection, stale local lease refusal, and
end-to-end checkpoint-handler integration with the deterministic checkpoint key.
No concrete production provider adapter exists yet, so this work establishes
and tests the interface rather than claiming a real external system is fenced.

### Reconciliation reference integrity and lease-loss crash window

The checkpoint reconciler now validates every non-null provider operation
reference returned by an adapter. Malformed references and references that
differ from the first durably recorded pending-operation handle are treated as
an unknown provider outcome. They cannot replace the original handle, authorize
a retry, or turn a mismatched operation into checkpoint success. The original
checkpoint remains indeterminate for a later operator/provider investigation.

A provider call may complete remotely while the worker loses its lease before
the response can be committed locally. The dispatch wrapper's postflight lease
check rejects the stale worker's result. The checkpoint runner then revalidates
ownership on the exception path, so the stale worker cannot mark the stage
failed or succeeded. The checkpoint remains executing and must be reconciled
using the stable idempotency key (and the persisted operation reference if one
was previously recorded) before any replay. This is intentionally conservative:
a remote success is not inferred from a lost local response.

New regression tests cover mismatched operation references, malformed
references, and a provider completing during lease takeover. The latter proves
that the remote call may have happened once while local checkpoint success
remains uncommitted and the checkpoint remains unresolved.

